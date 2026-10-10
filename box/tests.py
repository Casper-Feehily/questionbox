from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .mail import deliver_one, subscription_token
from .models import MailJob, Question, RateBucket, Subscription


def post(client, url, data=None, **extra):
    return client.post(url, urlencode(data or {"_form": "1"}), content_type="application/x-www-form-urlencoded", **extra)


class QuestionFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser(username="owner", password="private-test-password-123!")
        cls.member = get_user_model().objects.create_user(username="member", password="private-test-password-123!")

    def publish(self, question, answer="认真回答。", action="publish", notify="on"):
        self.client.force_login(self.owner)
        return post(self.client, reverse("owner_question", args=[question.pk]),
                    {"answer": answer, "expected_version": question.version, "action": action, "notify": notify})

    def test_pending_and_draft_stay_private_until_answered(self):
        self.assertEqual(post(self.client, reverse("ask"), {"text": "我的私人提问123", "signature": "小明"}).status_code, 302)
        question = Question.objects.get()
        self.assertNotContains(self.client.get("/"), question.text)
        self.assertEqual(self.client.get(reverse("question", args=[question.pk])).status_code, 404)
        self.publish(question, "私人草稿321", "draft")
        question.refresh_from_db()
        self.assertIsNone(question.answered_at)
        self.assertNotContains(self.client.get("/"), "私人草稿321")
        self.assertEqual(self.publish(question, "公开回答456").status_code, 302)
        question.refresh_from_db()
        public = self.client.get(reverse("question", args=[question.pk]))
        self.assertContains(public, "公开回答456")
        self.assertContains(public, "小明")
        self.publish(question, "编辑后的回答789")
        question.refresh_from_db()
        self.assertEqual(question.version, 2)
        self.assertContains(self.client.get(reverse("question", args=[question.pk])), "编辑后的回答789")

    def test_optional_signature_and_text_escaping(self):
        question = Question.objects.create(text='<script>alert("x")</script>', signature="<b>名字</b>")
        self.publish(question, '<img src="x" onerror="alert(1)">')
        response = self.client.get(reverse("question", args=[question.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "&lt;b&gt;名字&lt;/b&gt;")
        self.assertNotContains(response, '<img src="x"')
        self.assertEqual(Question(text="匿名提问").display_name, "匿名朋友")

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=False)
    def test_500_character_limit_and_no_uploads(self):
        self.assertEqual(post(self.client, "/ask/", {"text": "问" * 501}).status_code, 400)
        self.assertEqual(Question.objects.count(), 0)
        self.assertEqual(post(self.client, "/ask/", {"text": "问" * 500}).status_code, 302)
        self.assertEqual(Question.objects.get().text, "问" * 500)
        self.assertEqual(self.client.post("/ask/", {"text": "携带文件的请求"}).status_code, 415)
        self.assertEqual(self.client.post("/ask/", "x" * 17000, content_type="application/x-www-form-urlencoded").status_code, 413)

    def test_owner_permissions_and_csrf(self):
        question = Question.objects.create(text="这个问题等待回信")
        url = reverse("owner_question", args=[question.pk])
        self.assertEqual(post(self.client, url, {"answer": "越权回答", "expected_version": 0, "action": "publish"}).status_code, 302)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(post(self.client, reverse("owner_delete", args=[question.pk])).status_code, 404)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(post(csrf_client, url, {"answer": "无CSRF回答", "expected_version": 0, "action": "publish"}).status_code, 403)
        question.refresh_from_db()
        self.assertIsNone(question.answered_at)

    def test_edit_conflict_does_not_overwrite_answer(self):
        question = Question.objects.create(text="这个问题有两个编辑页面")
        self.publish(question, "第一个版本")
        self.assertEqual(self.publish(question, "旧页面想覆盖").status_code, 409)
        question.refresh_from_db()
        self.assertEqual(question.answer, "第一个版本")

    def test_full_length_answer_can_be_published(self):
        question = Question.objects.create(text="主人可以认真写一封长信吗")
        self.assertEqual(self.publish(question, "答" * 10000).status_code, 302)
        question.refresh_from_db()
        self.assertEqual(len(question.answer), 10000)
        self.assertEqual(self.publish(question, "答" * 10001).status_code, 400)

    def test_delete_removes_public_answer_subscription_and_jobs(self):
        question = Question.objects.create(text="需要删除的问题", answer="已有回答", answered_at=timezone.now())
        sub = Subscription.objects.create(question=question, email="private@example.com")
        MailJob.objects.create(subscription=sub, event="confirm")
        self.client.force_login(self.owner)
        url = reverse("owner_delete", args=[question.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(post(self.client, url).status_code, 302)
        self.assertFalse(Question.objects.exists())
        self.assertFalse(Subscription.objects.exists())
        self.assertFalse(MailJob.objects.exists())

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=False)
    def test_capacity_counts_all_questions_and_delete_reopens(self):
        now = timezone.now()
        Question.objects.bulk_create([Question(text=f"私人问题 {i}") for i in range(250)] +
                                    [Question(text=f"公开问题 {i}", answer="回信", answered_at=now, answer_updated_at=now) for i in range(250)])
        response = post(self.client, "/ask/", {"text": "试图成为第501条"})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "请联系 站长 解决后再投递", status_code=409)
        self.assertEqual(Question.objects.count(), 500)
        self.assertFalse(RateBucket.objects.exists())
        question = Question.objects.first()
        self.client.force_login(self.owner)
        post(self.client, reverse("owner_delete", args=[question.pk]))
        self.assertEqual(post(self.client, "/ask/", {"text": "删除后可以投递"}).status_code, 302)
        self.assertEqual(Question.objects.count(), 500)


@override_settings(QUESTION_RATE_LIMIT_ENABLED=True)
class RateLimitTests(TestCase):
    @override_settings(QUESTION_RATE_LIMIT_ENABLED=False, RATE_COOKIE_HOUR=1,
                       RATE_IP_HOUR=1, RATE_SITE_HOUR=1)
    def test_question_limits_can_be_disabled_with_existing_buckets_and_restored(self):
        with override_settings(QUESTION_RATE_LIMIT_ENABLED=True):
            self.assertEqual(post(self.client, "/ask/", {"text": "用完各项提交额度"}).status_code, 302)
        self.assertEqual(RateBucket.objects.count(), 4)
        for index in range(3):
            response = post(self.client, "/ask/", {"text": f"无需等待继续提交 {index}"})
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("Retry-After", response)
        self.assertEqual(post(Client(), "/ask/", {"text": "同IP的新访客继续提交"}).status_code, 302)
        self.assertEqual(Question.objects.count(), 5)
        self.assertTrue(all(count == 1 for count in RateBucket.objects.values_list("count", flat=True)))
        with override_settings(QUESTION_RATE_LIMIT_ENABLED=True):
            self.assertEqual(post(self.client, "/ask/", {"text": "重新开启后恢复限流"}).status_code, 429)

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=False)
    def test_login_attempts_are_throttled(self):
        for _ in range(5):
            response = post(self.client, reverse("login"), {"username": "missing", "password": "wrong"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(post(self.client, reverse("login"), {"username": "missing", "password": "wrong"}).status_code, 429)

    def test_cookie_limiter_is_independent_of_ip(self):
        first = post(self.client, "/ask/", {"text": "第一次匿名提问"})
        self.assertEqual(first.status_code, 302)
        response = post(self.client, "/ask/", {"text": "更换IP仍然被限流"}, REMOTE_ADDR="192.0.2.22")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertEqual(Question.objects.count(), 1)

    @override_settings(RATE_IP_HOUR=1)
    def test_reset_or_forged_cookie_and_forwarded_headers_do_not_bypass_ip(self):
        post(self.client, "/ask/", {"text": "第一次匿名提问"})
        other = Client()
        other.cookies[settings.VISITOR_COOKIE] = "forged-cookie"
        response = post(other, "/ask/", {"text": "伪造Cookie尝试"}, HTTP_X_FORWARDED_FOR="192.0.2.33")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Question.objects.count(), 1)
        self.assertNotEqual(response.cookies[settings.VISITOR_COOKIE].value, "forged-cookie")
        self.assertNotIn("127.0.0.1", " ".join(RateBucket.objects.values_list("key", flat=True)))

    def test_cookie_is_signed_and_expired_cookie_is_reissued(self):
        response = self.client.get("/")
        cookie = response.cookies[settings.VISITOR_COOKIE]
        request = RequestFactory().get("/", HTTP_COOKIE=f"{settings.VISITOR_COOKIE}={cookie.value}")
        visitor = request.get_signed_cookie(settings.VISITOR_COOKIE, salt=settings.VISITOR_COOKIE_SALT)
        self.assertEqual(len(visitor), 32)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        with override_settings(VISITOR_COOKIE_AGE=-1):
            renewed = self.client.get("/").cookies[settings.VISITOR_COOKIE]
        self.assertNotEqual(cookie.value, renewed.value)

    def test_hourly_cookie_budget_survives_minute_reset(self):
        start = timezone.now()
        for index in range(settings.RATE_COOKIE_HOUR):
            with patch("box.rate_limit.timezone.now", return_value=start + timedelta(seconds=index * 61)):
                self.assertEqual(post(self.client, "/ask/", {"text": f"按分钟投递第{index}条"}).status_code, 302)
        with patch("box.rate_limit.timezone.now", return_value=start + timedelta(seconds=settings.RATE_COOKIE_HOUR * 61)):
            self.assertEqual(post(self.client, "/ask/", {"text": "小时额度用完了"}).status_code, 429)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=True, TRUSTED_PROXY_CIDRS=())
    def test_untrusted_proxy_cannot_assert_https(self):
        self.assertEqual(self.client.get("/", HTTP_X_FORWARDED_PROTO="https").status_code, 301)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=True, TRUSTED_PROXY_CIDRS=(__import__('ipaddress').ip_network("127.0.0.1/32"),))
    def test_trusted_https_proxy_avoids_redirect_loop(self):
        response = self.client.get("/", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies[settings.VISITOR_COOKIE]["secure"])


@override_settings(EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="box@example.com")
class EmailTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(username="owner", password="private-test-password-123!")
        post(self.client, "/ask/", {"text": "可以通过邮件通知吗", "email": "Reader@example.com"})
        self.question = Question.objects.get()
        self.sub = self.question.subscription

    def publish(self, answer, action="publish"):
        self.question.refresh_from_db()
        self.client.force_login(self.owner)
        return post(self.client, reverse("owner_question", args=[self.question.pk]),
                    {"answer": answer, "expected_version": self.question.version, "action": action})

    def test_automatic_subscription_first_answer_edit_and_unsubscribe(self):
        self.assertIsNotNone(self.sub.subscribed_at)
        self.assertEqual(MailJob.objects.get().event, "subscribed")
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(self.question.text, mail.outbox[0].body)
        self.assertIn("已订阅回信", mail.outbox[0].subject)
        self.assertNotIn("/subscription/confirm/", mail.outbox[0].body)
        self.assertIn("/subscription/unsubscribe/", mail.outbox[0].body)
        self.publish("填写邮箱后自动收到的首次回答")
        self.assertTrue(MailJob.objects.filter(event="answered").exists())
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 2)
        self.publish("修改后的公开回答")
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 3)
        self.publish("修改后的公开回答")
        self.assertFalse(deliver_one())
        cancel_url = reverse("unsubscribe", args=[subscription_token(self.sub, "unsubscribe")])
        self.client.get(cancel_url)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.active)
        post(self.client, cancel_url)
        self.publish("退订后的答案更新")
        self.assertFalse(deliver_one())
        response = self.client.get(reverse("question", args=[self.question.pk]))
        self.assertNotContains(response, "reader@example.com")
        self.assertNotContains(response, subscription_token(self.sub, "unsubscribe"))

    def test_answer_does_not_wait_for_subscription_success_email(self):
        with patch("box.mail.send_mail", side_effect=OSError("temporary mail failure")):
            self.assertTrue(deliver_one())
        self.publish("订阅成功邮件重试时仍然能收到回信")
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("收到回答了", mail.outbox[0].subject)
        self.assertEqual(MailJob.objects.get(event="subscribed").status, "pending")

    def test_unsubscribe_before_delivery_cancels_welcome_and_answer_mail(self):
        self.publish("退订前还未发送的回答")
        url = reverse("unsubscribe", args=[subscription_token(self.sub, "unsubscribe")])
        post(self.client, url)
        self.assertEqual(MailJob.objects.filter(status="cancelled").count(), 2)
        self.assertFalse(deliver_one())

    def test_legacy_confirmation_links_still_work_without_changing_new_subscriptions(self):
        self.assertTrue(deliver_one())
        question = Question.objects.create(text="历史确认邮件对应的提问")
        sub = Subscription.objects.create(question=question, email="legacy@example.com", subscribed_at=None)
        MailJob.objects.create(subscription=sub, event="confirm")
        self.assertTrue(deliver_one())
        self.assertIn("确认订阅", mail.outbox[-1].subject)
        confirm = reverse("confirm_subscription", args=[subscription_token(sub, "confirm")])
        self.client.get(confirm)
        sub.refresh_from_db()
        self.assertIsNone(sub.subscribed_at)
        self.client.force_login(self.owner)
        post(self.client, reverse("owner_question", args=[question.pk]),
             {"answer": "历史确认前发布的回信", "expected_version": 0, "action": "publish"})
        self.assertFalse(MailJob.objects.filter(subscription=sub, event="answered").exists())
        post(self.client, confirm)
        sub.refresh_from_db()
        self.assertIsNotNone(sub.subscribed_at)
        self.assertTrue(deliver_one())
        self.assertEqual(MailJob.objects.get(subscription=sub, event="answered").status, "sent")

    def test_question_without_email_has_no_subscription_or_mail(self):
        self.assertTrue(deliver_one())
        question = Question.objects.create(text="不留下邮箱的提问")
        self.client.force_login(self.owner)
        post(self.client, reverse("owner_question", args=[question.pk]),
             {"answer": "没有邮箱时正常发布", "expected_version": 0, "action": "publish"})
        self.assertFalse(Subscription.objects.filter(question=question).exists())
        self.assertFalse(deliver_one())

    def test_draft_does_not_queue_answer_mail(self):
        self.publish("只保存草稿", "draft")
        self.assertEqual(MailJob.objects.count(), 1)

    def test_mail_errors_are_retried_without_rolling_back_question(self):
        with patch("box.mail.send_mail", side_effect=OSError("secret recipient must not be logged")):
            self.assertTrue(deliver_one())
        job = MailJob.objects.get()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.last_error, "OSError")
        self.assertEqual(job.attempts, 1)
        self.assertTrue(Question.objects.exists())
        self.assertFalse(deliver_one())
        MailJob.objects.update(available_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(deliver_one())
        self.assertEqual(MailJob.objects.get().status, "sent")

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=False)
    def test_email_subscription_rate_and_purpose_boundary(self):
        other = Client()
        response = post(other, "/ask/", {"text": "重复给同一邮箱发信", "email": "reader@example.com"}, REMOTE_ADDR="192.0.2.98")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Question.objects.count(), 1)
        wrong = subscription_token(self.sub, "unsubscribe")
        self.assertEqual(self.client.get(reverse("confirm_subscription", args=[wrong])).status_code, 404)


class ConcurrentCapacityTests(TransactionTestCase):
    def test_simultaneous_submissions_cannot_exceed_500(self):
        Question.objects.bulk_create([Question(text=f"已有提问 {i}") for i in range(499)])

        def submit(index):
            close_old_connections()
            try:
                return post(Client(), "/ask/", {"text": f"并发提问 {index}"}, REMOTE_ADDR=f"192.0.2.{index + 1}").status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(submit, range(6)))
        self.assertEqual(statuses.count(302), 1, statuses)
        self.assertEqual(statuses.count(409), 5, statuses)
        self.assertEqual(Question.objects.count(), 500)


class MaintenanceTests(TransactionTestCase):
    def test_online_backup_is_consistent_and_cannot_overwrite(self):
        from django.core.management.base import CommandError
        Question.objects.create(text="备份应该包含私人来信")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "backup.sqlite3"
            call_command("backup_database", output=path, stdout=StringIO())
            with sqlite3.connect(path) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(backup.execute("SELECT text FROM box_question").fetchone()[0], "备份应该包含私人来信")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(CommandError):
                call_command("backup_database", output=path, stdout=StringIO())
