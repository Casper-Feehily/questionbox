import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from uuid import uuid4

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from .models import (Question, Tag, BarkJob, Subscription, MailJob, ServerChanJob, WechatIdentity,
                     WechatSession, WechatReminder, WechatMessageJob, RateBucket)
from . import wechat

WX_CONFIG = dict(WECHAT_ENABLED=True, WECHAT_APPID="wx-test-app", WECHAT_APP_SECRET="private-test-secret",
                 WECHAT_REPLY_TEMPLATE_ID="reply-template", WECHAT_REPLY_TEMPLATE_FIELDS={"thing1": "notice", "time2": "time"},
                 WECHAT_MINIPROGRAM_STATE="trial", QUESTION_RATE_LIMIT_ENABLED=False, EMAIL_ENABLED=False,
                 SERVERCHAN_SENDKEY="")


def json_post(client, url, data, token=None):
    return client.post(url, json.dumps(data), content_type="application/json",
                       **({"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}))


def login(client, openid="visitor-one"):
    with patch("box.wechat.exchange_code", return_value=openid):
        response = json_post(client, "/api/v1/wechat/session/", {"code": "single-use-code"})
    assert response.status_code == 200, response.content
    return response.json()["token"]


@override_settings(**WX_CONFIG)
class MiniProgramApiTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = login(self.client)
        self.owner = get_user_model().objects.create_superuser("owner", password="private-password-123!")

    def ask(self, **fields):
        return json_post(self.client, "/api/v1/questions/", {
            "text": "一张私人便签", "client_request_id": str(uuid4()), **fields}, self.token)

    def get(self, path, token=None):
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def publish(self, question, text="公开回复", action="publish"):
        browser = Client()
        browser.force_login(self.owner)
        return browser.post(f"/owner/q/{question.pk}/", urlencode({
            "answer": text, "expected_version": question.version, "action": action}),
            content_type="application/x-www-form-urlencoded")

    def test_session_digest_scope_expiry_and_no_profile(self):
        identity = WechatIdentity.objects.get()
        session = WechatSession.objects.get()
        self.assertEqual(identity.openid, "visitor-one")
        self.assertEqual(session.token_digest, hashlib.sha256(self.token.encode()).hexdigest())
        self.assertNotEqual(session.token_digest, self.token)
        self.assertGreater(session.expires_at, timezone.now() + timedelta(days=29))
        login(self.client)
        self.assertEqual(WechatIdentity.objects.count(), 1)
        with override_settings(WECHAT_APPID="another-app"):
            self.assertEqual(self.get("/api/v1/me/questions/").status_code, 401)
        WechatSession.objects.filter(token_digest=session.token_digest).update(expires_at=timezone.now())
        self.assertEqual(self.get("/api/v1/me/questions/").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/me/questions/").status_code, 401)
        self.assertEqual(self.get("/api/v1/me/questions/", token="forged").status_code, 401)

    def test_browser_owner_session_cannot_replace_bearer(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/api/v1/me/questions/").status_code, 401)
        self.assertEqual(json_post(self.client, "/api/v1/questions/", {"text": "越权"}).status_code, 401)

    def test_pending_private_ownership_draft_and_publication_sync(self):
        result = self.ask()
        self.assertEqual(result.status_code, 201)
        q = Question.objects.get()
        self.assertEqual(self.client.get("/api/v1/questions/").json()["items"], [])
        self.assertEqual(self.client.get(f"/api/v1/questions/{q.pk}/").status_code, 404)
        other = login(Client(), "visitor-two")
        self.assertEqual(self.get(f"/api/v1/me/questions/{q.pk}/", other).status_code, 404)
        self.publish(q, "不应泄漏的草稿", "draft")
        mine = self.get(f"/api/v1/me/questions/{q.pk}/").json()["question"]
        self.assertEqual(mine["status"], "pending")
        self.assertIsNone(mine["answer"])
        self.assertNotIn("draft", mine)
        self.assertNotIn("不应泄漏的草稿", json.dumps(mine, ensure_ascii=False))
        self.publish(q)
        q.refresh_from_db()
        public = self.client.get(f"/api/v1/questions/{q.pk}/").json()["question"]
        self.assertEqual(public["answer"], "公开回复")
        self.publish(q, "新版本回复")
        public = self.client.get(f"/api/v1/questions/{q.pk}/").json()["question"]
        self.assertEqual(public["version"], 2)
        self.assertEqual(public["answer"], "新版本回复")
        self.publish(Question.objects.get(), "新的私人草稿", "draft")
        self.assertEqual(self.client.get(f"/api/v1/questions/{q.pk}/").json()["question"]["answer"], "新版本回复")
        self.assertNotContains(self.get(f"/api/v1/me/questions/{q.pk}/"), "新的私人草稿")

    def test_idempotency_conflict_and_replay_at_full_capacity(self):
        key = str(uuid4())
        self.assertEqual(self.ask(client_request_id=key).status_code, 201)
        with override_settings(MAX_QUESTIONS=1, QUESTION_RATE_LIMIT_ENABLED=True):
            replay = self.ask(client_request_id=key)
            self.assertEqual(replay.status_code, 200)
            self.assertFalse(replay.json()["created"])
            self.assertEqual(self.ask(text="不同内容", client_request_id=key).status_code, 409)
            self.assertEqual(self.ask().json()["error"]["code"], "box_full")
        self.assertEqual(Question.objects.count(), 1)

    @override_settings(EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SERVERCHAN_SENDKEY="SCTtest")
    def test_email_owner_alert_single_queue_and_no_private_fields(self):
        key = str(uuid4())
        response = self.ask(client_request_id=key, email="PRIVATE@example.com")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Subscription.objects.get().email, "private@example.com")
        self.assertEqual(MailJob.objects.count(), 1)
        self.assertEqual(ServerChanJob.objects.count(), 1)
        self.assertEqual(self.ask(client_request_id=key, email="private@example.com").status_code, 200)
        self.assertEqual(MailJob.objects.count(), 1)
        q = Question.objects.get()
        with override_settings(EMAIL_ENABLED=False):
            self.assertEqual(self.ask(client_request_id=key, email="private@example.com").status_code, 200)
            self.assertEqual(self.ask(client_request_id=key, email="invalid").status_code, 400)
        for forbidden in ["openid", "token", "email", "draft", "submission_digest", "client_request_id", "wechat_identity"]:
            self.assertNotIn(forbidden, response.json()["question"])
        self.assertNotIn("private@example.com", response.content.decode())
        self.publish(q)
        self.assertEqual(MailJob.objects.filter(event="answered").count(), 1)
        q.refresh_from_db()
        self.publish(q, "更新答案")
        self.assertEqual(MailJob.objects.filter(event="updated").count(), 1)

    def test_body_validation_unicode_limit_and_unknown_fields(self):
        self.assertEqual(self.ask(text="😀" * 700).status_code, 201)
        for fields in [{"text": "😀" * 701}, {"text": "  "}, {"signature": "署" * 31},
                       {"text": {"unexpected": "object"}}, {"email": "bad"}, {"openid": "forged"},
                       {"client_request_id": "not-a-uuid"}, {"website": "spam"}]:
            with self.subTest(fields=fields):
                self.assertEqual(self.ask(**fields).status_code, 400)
        for body in ["[]", "null", "broken"]:
            self.assertEqual(self.client.post("/api/v1/questions/", body, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/questions/", "x" * 17000, content_type="application/json").status_code, 413)
        self.assertEqual(self.client.post("/api/v1/questions/", {}, content_type="multipart/form-data").status_code, 415)
        self.assertEqual(self.client.delete("/api/v1/questions/").status_code, 405)

    def test_api_csrf_exemption_does_not_weaken_browser(self):
        self.assertEqual(self.ask().status_code, 201)
        self.client.force_login(self.owner)
        q = Question.objects.get()
        self.assertEqual(self.client.post(f"/owner/q/{q.pk}/delete/", "x=1", content_type="application/x-www-form-urlencoded").status_code, 403)
        self.assertEqual(self.client.post("/ask/", '{"text":"q"}', content_type="application/json").status_code, 415)
        self.assertEqual(self.client.get("/api/v1/unknown/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/unknown/")["Content-Type"], "application/json")

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=True)
    def test_wechat_identity_limit_survives_session_rotation_and_cookie_reset(self):
        self.assertEqual(self.ask().status_code, 201)
        self.token = login(self.client)
        self.client.cookies.clear()
        limited = self.ask()
        self.assertEqual(limited.status_code, 429)
        self.assertGreater(limited.json()["error"]["retry_after"], 0)
        self.assertEqual(limited["Retry-After"], str(limited.json()["error"]["retry_after"]))

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=True, RATE_IP_HOUR=1)
    def test_web_and_mini_program_share_ip_budget(self):
        web = Client()
        self.assertEqual(web.post("/ask/", "text=web", content_type="application/x-www-form-urlencoded").status_code, 302)
        self.assertEqual(self.ask().status_code, 429)

    def test_pagination_private_history_and_deleted_question(self):
        now = timezone.now()
        for n in range(12):
            Question.objects.create(text=f"历史 {n}", answer="回信", answered_at=now, answer_updated_at=now)
        self.assertEqual(len(self.client.get("/api/v1/questions/").json()["items"]), 5)
        self.assertTrue(self.client.get("/api/v1/questions/").json()["has_more"])
        self.assertEqual(len(self.client.get("/api/v1/questions/?page=3").json()["items"]), 2)
        self.assertEqual(self.get("/api/v1/me/questions/").json()["count"], 0)
        self.assertEqual(self.client.get("/api/v1/questions/?page=-1").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/questions/?page=99").json()["items"], [])
        qid = self.ask().json()["question"]["id"]
        Question.objects.get(pk=qid).delete()
        self.assertEqual(self.get(f"/api/v1/me/questions/{qid}/").status_code, 404)

    def test_public_browsing_matches_website_and_never_exposes_private_tags(self):
        now = timezone.now()
        topic = Tag.objects.create(name="学习")
        other = Tag.objects.create(name="生活")
        hidden = Tag.objects.create(name="私人标签")
        featured = Question.objects.create(text="精选", answer="回答", answered_at=now,
                                            answer_updated_at=now, is_featured=True)
        featured.tags.add(topic)
        ordinary = Question.objects.create(text="普通", answer="回答", answered_at=now, answer_updated_at=now)
        ordinary.tags.add(other)
        pending = Question.objects.create(text="未公开", draft="私人草稿", is_featured=True)
        pending.tags.add(hidden)
        base = "/api/v1/questions/"
        result = self.client.get(base, {"view": "featured", "tag": other.pk}).json()
        self.assertEqual([q["id"] for q in result["items"]], [str(featured.pk)])
        self.assertEqual(result["selected_view"], "featured")
        self.assertEqual(result["public_answer_count"], 2)
        self.assertEqual(result["public_topic_count"], 2)
        self.assertEqual({t["name"] for t in result["tags"]}, {"学习", "生活"})
        self.assertTrue(result["items"][0]["is_featured"])
        self.assertEqual(result["items"][0]["tags"], [{"id": topic.pk, "name": "学习"}])
        for params in [{"view": "featured"}, {"view": "all"}, {"view": "all", "tag": topic.pk},
                       {"view": "all", "tag": "invalid"}, {"view": "all", "tag": hidden.pk}]:
            with self.subTest(params=params):
                web = self.client.get("/", params).context
                data = self.client.get(base, params).json()
                self.assertEqual([q["id"] for q in data["items"]], [str(q.pk) for q in web["page"]])
                self.assertEqual(data["count"], web["answered_count"])
        self.assertEqual(self.client.get(base).json()["count"], 2)
        self.assertEqual(self.client.get(base, {"view": "all", "tag": other.pk}).json()["items"][0]["id"], str(ordinary.pk))
        self.assertNotContains(self.client.get(base), "私人标签")

    @override_settings(BARK_DEVICE_KEY="test-device", SITE_URL="https://ask.example.com")
    def test_mini_program_bark_submission_retry_and_delivery(self):
        key = str(uuid4())
        with patch("box.bark.send_notification") as send:
            self.assertEqual(self.ask(client_request_id=key).status_code, 201)
            self.assertEqual(self.ask(client_request_id=key).status_code, 200)
            self.assertEqual(BarkJob.objects.count(), 1)
            send.assert_not_called()
            from .bark import deliver_one
            self.assertTrue(deliver_one())
        self.assertEqual(BarkJob.objects.get().status, "sent")
        send.assert_called_once()
        title, body, url = send.call_args.args
        self.assertNotIn("一张私人便签", body)
        self.assertTrue(url.startswith("https://ask.example.com/owner/q/"))
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_public_page_size_and_classification_update_sync(self):
        now = timezone.now()
        questions = [Question.objects.create(text=f"问题{n}", answer="回答", answered_at=now,
                       answer_updated_at=now) for n in range(15)]
        result = self.client.get("/api/v1/questions/?view=all&per_page=14").json()
        self.assertEqual(len(result["items"]), 14)
        self.assertTrue(result["has_more"])
        q = questions[0]
        q.is_featured = True
        q.save()
        self.assertEqual(self.client.get("/api/v1/questions/?view=featured").json()["count"], 1)
        q.delete()
        self.assertEqual(self.client.get("/api/v1/questions/?view=featured").json()["count"], 0)

    def test_disabled_capabilities_and_login_provider_failures(self):
        with override_settings(WECHAT_ENABLED=False):
            meta = self.client.get("/api/v1/meta/").json()
            self.assertFalse(meta["wechat_enabled"])
            self.assertFalse(meta["reminders_enabled"])
            self.assertIsNone(meta["reply_template_id"])
            self.assertEqual(self.ask().status_code, 503)
            self.assertEqual(self.client.get("/api/v1/questions/").status_code, 200)
        with patch("box.wechat.exchange_code", side_effect=wechat.WechatError(40163)):
            self.assertEqual(json_post(self.client, "/api/v1/wechat/session/", {"code": "used"}).status_code, 401)
        with patch("box.wechat.exchange_code", side_effect=requests.Timeout("private-secret")):
            response = json_post(self.client, "/api/v1/wechat/session/", {"code": "new"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private-secret", response.content.decode())

    def test_login_has_independent_rate_limit(self):
        RateBucket.objects.all().update(count=30)
        with patch("box.wechat.exchange_code") as provider:
            response = json_post(self.client, "/api/v1/wechat/session/", {"code": "new"})
        self.assertEqual(response.status_code, 429)
        provider.assert_not_called()

    def test_reminder_ownership_late_subscribe_and_no_edit_duplicates(self):
        self.ask()
        q = Question.objects.get()
        url = f"/api/v1/me/questions/{q.pk}/wechat-reminder/"
        other = login(Client(), "visitor-two")
        self.assertEqual(json_post(self.client, url, {"enabled": True}, other).status_code, 404)
        self.assertEqual(json_post(self.client, url, {"enabled": "yes"}, self.token).status_code, 400)
        self.publish(q)
        self.assertFalse(WechatMessageJob.objects.exists())
        self.assertEqual(json_post(self.client, url, {"enabled": True}, self.token).status_code, 200)
        self.assertEqual(WechatMessageJob.objects.count(), 1)
        q.refresh_from_db()
        self.publish(q, "第二次回复")
        json_post(self.client, url, {"enabled": True}, self.token)
        self.assertEqual(WechatMessageJob.objects.count(), 1)
        json_post(self.client, url, {"enabled": False}, self.token)
        self.assertEqual(WechatMessageJob.objects.get().status, "cancelled")
        json_post(self.client, url, {"enabled": True}, self.token)
        self.assertEqual(WechatMessageJob.objects.get().status, "pending")
        Question.objects.get().delete()
        self.assertFalse(WechatReminder.objects.exists())
        self.assertFalse(WechatMessageJob.objects.exists())

    def test_subscribe_before_publish_and_drafts_do_not_queue(self):
        self.ask()
        q = Question.objects.get()
        json_post(self.client, f"/api/v1/me/questions/{q.pk}/wechat-reminder/", {"enabled": True}, self.token)
        self.publish(q, "私人草稿", "draft")
        self.assertFalse(WechatMessageJob.objects.exists())
        self.publish(q)
        self.assertEqual(WechatMessageJob.objects.count(), 1)


@override_settings(**WX_CONFIG)
class WechatProviderTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        identity = WechatIdentity.objects.create(appid=settings.WECHAT_APPID, openid="recipient-secret")
        self.question = Question.objects.create(text="私人原文", signature="私人署名", answer="公开答案", answered_at=timezone.now(),
                                                wechat_identity=identity)
        WechatReminder.objects.create(question=self.question)
        wechat.queue_reply(self.question)

    def response(self, value):
        response = Mock()
        response.json.return_value = value
        return response

    def test_code_exchange_does_not_return_session_key(self):
        with patch("box.wechat.requests.get", return_value=self.response({"openid": "wx-user", "session_key": "private-key"})) as get:
            self.assertEqual(wechat.exchange_code("login-code"), "wx-user")
        self.assertEqual(get.call_args.kwargs["timeout"], 10)

    def test_provider_payload_validation(self):
        for result in [[], {}, {"openid": 123}, {"errcode": True}, {"errcode": "0"}]:
            with self.subTest(result=result), patch("box.wechat.requests.get", return_value=self.response(result)):
                with self.assertRaises(wechat.WechatError):
                    wechat.exchange_code("code")

    def test_token_cache_and_generic_send_payload(self):
        with (patch("box.wechat.requests.get", return_value=self.response({"access_token": "access-secret", "expires_in": 7200})) as get,
              patch("box.wechat.requests.post", return_value=self.response({"errcode": 0})) as post):
            wechat.send_reply(self.question)
            wechat.send_reply(self.question)
        get.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["touser"], "recipient-secret")
        self.assertEqual(payload["page"], f"pages/detail/index?id={self.question.pk}&mine=1")
        self.assertEqual(payload["miniprogram_state"], "trial")
        self.assertNotIn(self.question.text, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(self.question.signature, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(self.question.answer, json.dumps(payload, ensure_ascii=False))

    def test_success_and_repeated_publish_never_send_twice(self):
        with patch("box.wechat.send_reply") as send:
            self.assertTrue(wechat.deliver_one())
            wechat.queue_reply(self.question)
            self.assertFalse(wechat.deliver_one())
        send.assert_called_once()
        self.assertEqual(WechatMessageJob.objects.get().status, "sent")

    def test_transient_errors_retry_five_times_redact_and_requeue(self):
        with patch("box.wechat.send_reply", side_effect=wechat.WechatError(-1)):
            for n in range(1, 6):
                WechatMessageJob.objects.update(available_at=timezone.now() - timedelta(seconds=1))
                self.assertTrue(wechat.deliver_one())
                self.assertEqual(WechatMessageJob.objects.get().attempts, n)
        job = WechatMessageJob.objects.get()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.last_error, "APICode:-1")
        call_command("retry_failed_wechat", stdout=StringIO())
        self.assertEqual(WechatMessageJob.objects.get().status, "pending")

    def test_no_permission_is_terminal(self):
        with patch("box.wechat.send_reply", side_effect=wechat.WechatError(43101)):
            self.assertTrue(wechat.deliver_one())
        self.assertEqual(WechatMessageJob.objects.get().status, "rejected")
        self.assertEqual(wechat.retry_failed(), 0)

    def test_uncertain_network_result_is_preserved_not_auto_retried(self):
        with patch("box.wechat.access_token", return_value="access-secret"), patch("box.wechat.requests.post", side_effect=requests.Timeout("access-secret")):
            self.assertTrue(wechat.deliver_one())
        job = WechatMessageJob.objects.get()
        self.assertEqual(job.status, "uncertain")
        self.assertNotIn("access-secret", job.last_error)
        self.assertFalse(wechat.deliver_one())
        self.assertEqual(wechat.retry_failed(), 0)
        with self.assertRaises(CommandError):
            call_command("retry_failed_wechat", include_uncertain=True, stdout=StringIO())
        call_command("retry_failed_wechat", job=job.pk, include_uncertain=True, stdout=StringIO())
        self.assertEqual(WechatMessageJob.objects.get().status, "pending")

    def test_expired_lease_requires_investigation(self):
        WechatMessageJob.objects.update(status="sending", lease_until=timezone.now() - timedelta(seconds=1))
        with patch("box.wechat.send_reply") as send:
            self.assertFalse(wechat.deliver_one())
            send.assert_not_called()
        self.assertEqual(WechatMessageJob.objects.get().status, "uncertain")

    def test_disabled_or_cancelled_jobs_do_not_send(self):
        with override_settings(WECHAT_REPLY_TEMPLATE_ID=""), patch("box.wechat.send_reply") as send:
            self.assertFalse(wechat.deliver_one())
            send.assert_not_called()
        WechatReminder.objects.update(active=False)
        with patch("box.wechat.send_reply") as send:
            self.assertTrue(wechat.deliver_one())
            send.assert_not_called()
        self.assertEqual(WechatMessageJob.objects.get().status, "cancelled")

    def test_worker_cleans_sessions_without_removing_sent_sentinel(self):
        token, _ = wechat.create_session("recipient-secret")
        WechatSession.objects.update(expires_at=timezone.now() - timedelta(days=1))
        with patch("box.wechat.send_reply"):
            call_command("mail_worker", once=True, stdout=StringIO())
        self.assertFalse(WechatSession.objects.exists())
        self.assertEqual(WechatMessageJob.objects.get().status, "sent")

    def test_owner_retry_is_protected_and_excludes_uncertain(self):
        WechatMessageJob.objects.update(status="uncertain")
        owner = get_user_model().objects.create_superuser("owner", password="private-password-123!")
        url = "/owner/wechat/retry/"
        client = Client(enforce_csrf_checks=True)
        client.force_login(owner)
        self.assertEqual(client.post(url, "x=1", content_type="application/x-www-form-urlencoded").status_code, 403)
        browser = Client()
        browser.force_login(owner)
        self.assertContains(browser.get("/owner/"), "微信回复提醒失败")
        self.assertEqual(browser.post(url, "x=1", content_type="application/x-www-form-urlencoded").status_code, 302)
        self.assertEqual(WechatMessageJob.objects.get().status, "uncertain")


@override_settings(**WX_CONFIG, MAX_QUESTIONS=1)
class MiniProgramConcurrencyTests(TransactionTestCase):
    def setUp(self):
        _, _ = wechat.create_session("visitor-one")
        self.identity = WechatIdentity.objects.get()

    def submit(self, request_id):
        from django.test import RequestFactory
        from .submission import submit_question, SubmissionError
        close_old_connections()
        request = RequestFactory().post("/")
        request.client_address, request.visitor_id = "127.0.0.1", "visitor"
        try:
            question, created = submit_question(request, {"text": "同一张便签", "signature": "", "email": ""}, self.identity, request_id)
            return str(question.pk), created
        except SubmissionError:
            return None, False
        finally:
            close_old_connections()

    def test_concurrent_replays_and_capacity(self):
        key = uuid4()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.submit, [key, key]))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(Question.objects.count(), 1)
        Question.objects.all().delete()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.submit, [uuid4(), uuid4()]))
        self.assertEqual(Question.objects.count(), 1)
        self.assertEqual(sum(created for _, created in results), 1)


class MiniProgramMigrationTests(TransactionTestCase):
    def test_existing_questions_subscriptions_and_jobs_survive_without_claiming_identity(self):
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        old_target = [("box", "0003_automatic_subscriptions")]
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            old_question = old_apps.get_model("box", "Question")
            first = old_question.objects.create(text="历史私人便签", draft="历史草稿")
            second = old_question.objects.create(text="历史公开便签", answer="历史回信", answered_at=timezone.now(), version=2)
            sub = old_apps.get_model("box", "Subscription").objects.create(question=first, email="history@example.com", subscribed_at=timezone.now())
            old_apps.get_model("box", "MailJob").objects.create(subscription=sub, event="subscribed")
            MigrationExecutor(connection).migrate(leaves)
            self.assertEqual(Question.objects.get(pk=first.pk).draft, "历史草稿")
            self.assertEqual(Question.objects.get(pk=second.pk).answer, "历史回信")
            self.assertEqual(Question.objects.get(pk=second.pk).version, 2)
            self.assertFalse(Question.objects.exclude(wechat_identity=None).exists())
            self.assertFalse(Question.objects.exclude(client_request_id=None).exists())
            self.assertEqual(Subscription.objects.get().email, "history@example.com")
            self.assertEqual(MailJob.objects.count(), 1)
        finally:
            MigrationExecutor(connection).migrate(leaves)
