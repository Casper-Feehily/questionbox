import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Question, ServerChanJob
from .serverchan import ServerChanError, deliver_one, queue_question, send_notification


@override_settings(SERVERCHAN_SENDKEY="SCT123TestKey", EMAIL_ENABLED=False,
                   QUESTION_RATE_LIMIT_ENABLED=False, SITE_URL="https://ask.example.com")
class ServerChanTests(TransactionTestCase):
    def ask(self, **fields):
        return self.client.post("/ask/", urlencode({"text": "私人提问内容", **fields}),
                                content_type="application/x-www-form-urlencoded")

    def test_accepted_question_queues_once_without_http_on_request(self):
        with patch("box.serverchan.urlopen") as request:
            self.assertEqual(self.ask().status_code, 302)
            question = Question.objects.get()
            queue_question(question)
            request.assert_not_called()
        self.assertEqual(ServerChanJob.objects.count(), 1)
        self.assertEqual(ServerChanJob.objects.get().question_id, question.pk)

    @override_settings(SERVERCHAN_SENDKEY="")
    def test_missing_key_keeps_question_flow_and_worker_disabled(self):
        self.assertEqual(self.ask().status_code, 302)
        self.assertFalse(ServerChanJob.objects.exists())
        with patch("box.serverchan.urlopen") as request:
            self.assertFalse(deliver_one())
            request.assert_not_called()

    @override_settings(MAX_QUESTIONS=1)
    def test_invalid_spam_and_full_box_do_not_queue(self):
        self.assertEqual(self.ask(text="问" * 701).status_code, 400)
        self.assertEqual(self.ask(website="https://spam.example.com").status_code, 302)
        self.assertFalse(ServerChanJob.objects.exists())
        self.assertEqual(self.ask().status_code, 302)
        self.assertEqual(self.ask(text="箱满拒收").status_code, 409)
        self.assertEqual(ServerChanJob.objects.count(), 1)

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=True)
    def test_rate_limited_submission_does_not_queue(self):
        self.assertEqual(self.ask().status_code, 302)
        self.assertEqual(self.ask().status_code, 429)
        self.assertEqual(ServerChanJob.objects.count(), 1)

    @override_settings(EMAIL_ENABLED=True)
    def test_question_and_notification_roll_back_together(self):
        with patch("box.views.Subscription.objects.create", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.ask(email="visitor@example.com")
        self.assertFalse(Question.objects.exists())
        self.assertFalse(ServerChanJob.objects.exists())

    def test_worker_sends_private_link_even_when_email_is_disabled(self):
        self.ask(signature="私人署名")
        question = Question.objects.get()
        with patch("box.serverchan.send_notification") as send:
            call_command("mail_worker", once=True, stdout=StringIO())
        title, body = send.call_args.args
        self.assertLessEqual(len(title), 32)
        self.assertIn("https://ask.example.com" + reverse("owner_question", args=[question.pk]), body)
        self.assertNotIn(question.text, body)
        self.assertNotIn(question.signature, body)
        self.assertEqual(ServerChanJob.objects.get().status, "sent")
        self.assertFalse(deliver_one())
        self.assertEqual(self.client.get(reverse("owner_question", args=[question.pk])).status_code, 302)
        self.assertEqual(self.client.get(reverse("question", args=[question.pk])).status_code, 404)

    def test_post_encoding_timeout_and_turbo_endpoint(self):
        with patch("box.serverchan.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"code":0}'
            send_notification("中文测试", "正文 & 换行\n下一行")
        sent_request = request.call_args.args[0]
        self.assertEqual(sent_request.full_url, "https://sctapi.ftqq.com/SCT123TestKey.send")
        self.assertEqual(sent_request.get_method(), "POST")
        self.assertEqual(parse_qs(sent_request.data.decode()),
                         {"title": ["中文测试"], "desp": ["正文 & 换行\n下一行"], "noip": ["1"]})
        self.assertEqual(request.call_args.kwargs["timeout"], 10)

    @override_settings(SERVERCHAN_SENDKEY="sctp123tTestKey")
    def test_sc3_endpoint(self):
        with patch("box.serverchan.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"code":0}'
            send_notification("测试", "正文")
        self.assertEqual(request.call_args.args[0].full_url, "https://123.push.ft07.com/send/sctp123tTestKey.send")

    def test_api_errors_retry_stop_at_five_and_can_be_requeued(self):
        self.ask()
        with patch("box.serverchan.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"code": 40001, "message": "private key must not be stored"}).encode()
            for attempt in range(1, 6):
                ServerChanJob.objects.update(available_at=timezone.now() - timedelta(seconds=1))
                self.assertTrue(deliver_one())
                job = ServerChanJob.objects.get()
                self.assertEqual(job.attempts, attempt)
                self.assertEqual(job.last_error, "APICode:40001")
                self.assertEqual(job.status, "failed" if attempt == 5 else "pending")
                self.assertFalse(deliver_one())
        self.assertTrue(Question.objects.exists())
        call_command("retry_failed_notifications", job=job.pk, stdout=StringIO())
        with patch("box.serverchan.send_notification"):
            self.assertTrue(deliver_one())
        job.refresh_from_db()
        self.assertEqual(job.status, "sent")
        self.assertEqual(job.attempts, 1)

    def test_network_exception_does_not_store_key_or_url(self):
        self.ask()
        with patch("box.serverchan.urlopen", side_effect=URLError("https://sctapi.ftqq.com/SCT123TestKey.send")):
            self.assertTrue(deliver_one())
        self.assertEqual(ServerChanJob.objects.get().last_error, "URLError")

    def test_unexpired_lease_is_not_sent_and_expired_lease_is_recovered(self):
        self.ask()
        ServerChanJob.objects.update(status="sending", lease_until=timezone.now() + timedelta(minutes=3))
        with patch("box.serverchan.send_notification") as send:
            self.assertFalse(deliver_one())
            send.assert_not_called()
            ServerChanJob.objects.update(lease_until=timezone.now() - timedelta(seconds=1))
            self.assertTrue(deliver_one())
            send.assert_called_once()

    def test_deletion_cancels_queued_notification(self):
        self.ask()
        Question.objects.get().delete()
        self.assertFalse(ServerChanJob.objects.exists())
        with patch("box.serverchan.send_notification") as send:
            self.assertFalse(deliver_one())
            send.assert_not_called()

    def test_invalid_key_and_malformed_response(self):
        with override_settings(SERVERCHAN_SENDKEY="SCTbad/redirect"):
            with patch("box.serverchan.urlopen") as request:
                with self.assertRaisesMessage(ServerChanError, "InvalidSendKey"):
                    send_notification("测试", "正文")
                request.assert_not_called()
        for payload in [b'[]', b'{}', b'{"code": false}', b'{"code": "0"}']:
            with self.subTest(payload=payload), patch("box.serverchan.urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = payload
                with self.assertRaisesMessage(ServerChanError, "InvalidResponse"):
                    send_notification("测试", "正文")

    def test_test_command_creates_no_question_and_redacts_network_error(self):
        with patch("box.serverchan.urlopen", side_effect=URLError("SCT123TestKey")):
            with self.assertRaisesMessage(CommandError, "ServerChan test failed: URLError"):
                call_command("test_serverchan", stdout=StringIO())
        self.assertFalse(Question.objects.exists())
        self.assertFalse(ServerChanJob.objects.exists())
        with patch("box.management.commands.test_serverchan.send_notification") as send:
            call_command("test_serverchan", stdout=StringIO())
            send.assert_called_once()

    @override_settings(SERVERCHAN_SENDKEY="")
    def test_test_command_requires_a_key(self):
        with self.assertRaisesMessage(CommandError, "Set SERVERCHAN_SENDKEY"):
            call_command("test_serverchan", stdout=StringIO())
