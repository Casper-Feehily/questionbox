import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import MailJob, Question, Subscription, WechatMessageJob
from .tests import post


@override_settings(EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AnswerAutosaveTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("autosave-owner", password="test-password")
        self.client.force_login(self.owner)
        self.question = Question.objects.create(text="需要慢慢写的问题")
        Subscription.objects.create(question=self.question, email="reader@example.com")
        self.url = reverse("owner_question", args=[self.question.pk])

    def save(self, answer, expected_draft="", expected_version=0, action="autosave"):
        return post(self.client, self.url, {
            "answer": answer, "expected_version": expected_version,
            "expected_draft": expected_draft, "action": action,
        })

    def test_autosave_restores_exact_private_text_without_notifications(self):
        answer = "  未写完的回信\n下一段\n"
        response = self.save(answer)
        self.assertEqual(response.status_code, 200)
        self.question.refresh_from_db()
        self.assertEqual(self.question.draft, answer)
        self.assertEqual(self.question.answer, "")
        self.assertEqual(self.question.version, 0)
        self.assertIsNone(self.question.answered_at)
        self.assertFalse(MailJob.objects.exists())
        self.assertFalse(WechatMessageJob.objects.exists())
        self.assertEqual(self.client.get(self.url).context["form"]["answer"].value(), answer)
        self.assertNotContains(self.client.get("/"), "未写完的回信")

    def test_empty_draft_survives_reload_of_published_answer(self):
        self.question.answer = "已经公开的回答"
        self.question.answered_at = self.question.answer_updated_at = timezone.now()
        self.question.version = 1
        self.question.save()
        response = self.save("", expected_version=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url).context["form"]["answer"].value(), "")
        public = self.client.get(reverse("question", args=[self.question.pk]))
        self.assertContains(public, "已经公开的回答")
        self.assertFalse(MailJob.objects.exists())
        self.assertEqual(self.save("", expected_draft=response.json()["draft_revision"],
                                   expected_version=1, action="publish").status_code, 400)

    def test_stale_page_cannot_overwrite_newer_draft_or_publish_over_it(self):
        first = self.save("新草稿")
        self.assertEqual(first.status_code, 200)
        for action in ("autosave", "draft", "publish"):
            self.assertEqual(self.save("旧页面的内容", action=action).status_code, 409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.draft, "新草稿")
        second = self.save("继续编辑", expected_draft=first.json()["draft_revision"])
        self.assertEqual(second.status_code, 200)

    def test_publication_clears_draft_and_rejects_late_autosave(self):
        saved = self.save("准备发布")
        self.assertEqual(saved.status_code, 200)
        revision = saved.json()["draft_revision"]
        self.assertEqual(self.save("正式回答", expected_draft=revision, action="publish").status_code, 302)
        self.assertEqual(self.save("迟到的草稿", expected_draft=revision).status_code, 409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.answer, "正式回答")
        self.assertEqual(self.question.draft, "")
        self.assertIsNone(self.question.draft_revision)
        self.assertEqual(self.client.get(self.url).context["form"]["answer"].value(), "正式回答")
        self.assertEqual(MailJob.objects.count(), 1)

    def test_invalid_autosave_never_overwrites_existing_draft(self):
        self.question.draft = "原有草稿"
        self.question.save()
        for answer in ("字" * 10001, "\x00"):
            response = self.save(answer)
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json())
        self.assertEqual(post(self.client, self.url, {
            "action": "autosave", "answer": "缺少版本", "expected_version": 0,
        }).status_code, 400)
        self.question.refresh_from_db()
        self.assertEqual(self.question.draft, "原有草稿")

    def test_autosave_requires_owner_and_csrf(self):
        anonymous = Client()
        self.assertEqual(post(anonymous, self.url, {"action": "autosave"}).status_code, 302)
        member = get_user_model().objects.create_user("autosave-member")
        anonymous.force_login(member)
        self.assertEqual(post(anonymous, self.url, {"action": "autosave"}).status_code, 404)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(post(csrf_client, self.url, {"action": "autosave"}).status_code, 403)
        self.question.refresh_from_db()
        self.assertEqual(self.question.draft, "")

    def test_retry_after_lost_response_is_idempotent_but_cannot_overwrite_newer_save(self):
        data = {"action": "autosave", "answer": "响应丢失的草稿", "expected_version": 0,
                "expected_draft": "", "save_id": str(uuid.uuid4())}
        first = post(self.client, self.url, data)
        self.assertEqual(first.status_code, 200)
        retried = post(self.client, self.url, data)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json(), first.json())
        self.assertEqual(self.save("后续草稿", expected_draft=first.json()["draft_revision"]).status_code, 200)
        self.assertEqual(post(self.client, self.url, data).status_code, 409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.draft, "后续草稿")
