import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import graph
from .mail import deliver_one, subscription_token
from .models import MailJob, Question
from .tests import post


@override_settings(EMAIL_ENABLED=True, EMAIL_BACKEND=graph.BACKEND,
                   MS_GRAPH_CLIENT_ID="11111111-1111-4111-8111-111111111111",
                   MS_GRAPH_TENANT_ID="22222222-2222-4222-8222-222222222222",
                   MS_GRAPH_ACCOUNT_TYPE="organization", MS_GRAPH_CLIENT_SECRET="test-client-secret",
                   SITE_URL="https://ask.example.com")
class GraphTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser("graph-owner", password="private-test-password-123!")
        cls.member = get_user_model().objects.create_user("graph-member", password="private-test-password-123!")

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = override_settings(DATA_DIR=Path(self.directory.name))
        self.env.enable()
        self.addCleanup(self.env.disable)
        self.account = {"home_account_id": "test-account-id", "username": "sender@example.com"}
        self.flow = {"auth_uri": "https://login.microsoftonline.com/authorize", "state": "unguessable-state",
                     "code_verifier": "private-pkce-verifier"}

    def save_flow(self, created_at=None):
        session = self.client.session
        session[graph.FLOW_SESSION_KEY] = {"flow": self.flow, "created_at": created_at or time.time(),
                                          "client_id": graph.settings.MS_GRAPH_CLIENT_ID, "authority": graph.authority()}
        session.save()

    def connect_cache(self):
        with graph._store() as state:
            state.update(client_id=graph.settings.MS_GRAPH_CLIENT_ID, authority=graph.authority(),
                         account_id=self.account["home_account_id"], sender=self.account["username"],
                         cache=json.dumps({"private-cache": "private-refresh-token"}), requires_reauth=False)

    def test_mail_pages_and_mutations_are_owner_only_and_csrf_protected(self):
        self.assertEqual(self.client.get(reverse("owner_mail")).status_code, 302)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("owner_mail")).status_code, 404)
        self.assertEqual(post(self.client, reverse("mail_disconnect")).status_code, 404)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        for name in ("mail_connect", "mail_disconnect", "mail_test"):
            self.assertEqual(post(csrf, reverse(name)).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("mail_connect")).status_code, 405)

    def test_unconnected_mailbox_hides_email_and_rejects_direct_subscription(self):
        self.assertFalse(graph.notifications_enabled())
        self.assertNotContains(self.client.get("/"), 'name="email"')
        self.assertEqual(post(self.client, "/ask/", {"text": "没有邮箱授权时", "email": "reader@example.com"}).status_code, 400)
        self.assertFalse(Question.objects.exists())

    def test_authorization_uses_exact_callback_and_private_session(self):
        self.client.force_login(self.owner)
        with patch("box.graph._application") as factory:
            factory.return_value.initiate_auth_code_flow.return_value = self.flow
            response = post(self.client, reverse("mail_connect"))
            factory.return_value.initiate_auth_code_flow.assert_called_once_with(
                graph.SCOPES, redirect_uri="https://ask.example.com/owner/mail/callback/",
                prompt="select_account", response_mode="query")
        self.assertEqual(response.url, self.flow["auth_uri"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.client.session[graph.FLOW_SESSION_KEY]["flow"], self.flow)
        self.assertNotContains(self.client.get(reverse("owner_mail")), "private-pkce-verifier")

    def test_missing_expired_duplicate_or_wrong_state_never_exchanges_a_code(self):
        self.client.force_login(self.owner)
        with patch("box.graph.complete_authorization") as exchange:
            self.client.get(reverse("mail_callback"), {"state": self.flow["state"], "code": "private-code"})
            for query in ("state=wrong&code=private-code", "state=unguessable-state&state=other&code=private-code"):
                self.save_flow()
                self.client.get(reverse("mail_callback") + "?" + query)
            self.save_flow(time.time() - 601)
            self.client.get(reverse("mail_callback"), {"state": self.flow["state"], "code": "private-code"})
            exchange.assert_not_called()
        self.assertNotIn(graph.FLOW_SESSION_KEY, self.client.session)
        self.assertFalse(graph.notifications_enabled())

    def test_valid_callback_saves_encrypted_tokens_and_cannot_be_replayed(self):
        self.client.force_login(self.owner)
        self.save_flow()

        def application(cache):
            cache.deserialize(json.dumps({"private-cache": "private-refresh-token"}))
            app = Mock()
            app.get_accounts.return_value = [self.account]
            app.acquire_token_by_auth_code_flow.return_value = {"access_token": "private-access-token", "scope": "Mail.Send"}
            return app

        with patch("box.graph._application", side_effect=application) as factory:
            response = self.client.get(reverse("mail_callback"), {"state": self.flow["state"], "code": "private-code"})
            self.assertRedirects(response, reverse("owner_mail"))
            self.client.get(reverse("mail_callback"), {"state": self.flow["state"], "code": "private-code"})
            self.assertEqual(factory.call_count, 1)
        self.assertTrue(graph.notifications_enabled())
        path = Path(self.directory.name) / "outlook-tokens.enc"
        self.assertNotIn(b"private-refresh-token", path.read_bytes())
        self.assertNotIn(b"sender@example.com", path.read_bytes())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotContains(self.client.get("/"), "sender@example.com")
        self.assertContains(self.client.get("/ask/"), 'name="email"')

    def test_failed_authorization_keeps_existing_mailbox_and_hides_provider_details(self):
        self.connect_cache()
        before = (Path(self.directory.name) / "outlook-tokens.enc").read_bytes()
        self.client.force_login(self.owner)
        self.save_flow()
        with patch("box.graph._application") as factory:
            factory.return_value.acquire_token_by_auth_code_flow.return_value = {
                "error": "invalid_grant", "error_description": "private-provider-information"}
            response = self.client.get(reverse("mail_callback"), {"state": self.flow["state"], "error": "access_denied"}, follow=True)
        self.assertNotContains(response, "private-provider-information")
        self.assertEqual((Path(self.directory.name) / "outlook-tokens.enc").read_bytes(), before)
        self.assertTrue(graph.notifications_enabled())

    def test_missing_send_permission_cannot_replace_mailbox(self):
        with patch("box.graph._application") as factory:
            factory.return_value.acquire_token_by_auth_code_flow.return_value = {"access_token": "private", "scope": "User.Read"}
            factory.return_value.get_accounts.return_value = [self.account]
            with self.assertRaises(graph.GraphError):
                graph.complete_authorization(self.flow, {"state": self.flow["state"], "code": "code"})
        self.assertFalse(graph.notifications_enabled())

    def test_refresh_persists_new_token_and_revocation_closes_subscription_entry(self):
        self.connect_cache()

        def application(cache):
            app = Mock()
            app.get_accounts.return_value = [self.account]

            def refresh(*args, **kwargs):
                cache.deserialize(json.dumps({"private-cache": "rotated-refresh-token"}))
                cache.has_state_changed = True
                return {"access_token": "fresh-access-token"}

            app.acquire_token_silent_with_error.side_effect = refresh
            return app

        with patch("box.graph._application", side_effect=application):
            self.assertEqual(graph.access_token(), "fresh-access-token")
        with graph._store() as state:
            self.assertIn("rotated-refresh-token", state["cache"])
        with patch("box.graph._application") as factory:
            factory.return_value.get_accounts.return_value = [self.account]
            factory.return_value.acquire_token_silent_with_error.return_value = {"error": "invalid_grant"}
            with self.assertRaises(graph.GraphReauthorizationRequired):
                graph.access_token()
        self.assertFalse(graph.notifications_enabled())
        self.assertNotContains(self.client.get("/"), 'name="email"')

    def test_temporary_refresh_failure_preserves_connection(self):
        self.connect_cache()
        with patch("box.graph._application") as factory:
            factory.return_value.get_accounts.return_value = [self.account]
            factory.return_value.acquire_token_silent_with_error.return_value = {"error": "temporarily_unavailable"}
            with self.assertRaises(graph.GraphError):
                graph.access_token()
        self.assertTrue(graph.notifications_enabled())

    def test_app_change_secret_key_change_or_disconnect_disables_old_authorization(self):
        self.connect_cache()
        with override_settings(MS_GRAPH_CLIENT_ID="00000000-0000-0000-0000-000000000001"):
            self.assertFalse(graph.notifications_enabled())
        with override_settings(SECRET_KEY="different-encryption-key-for-test-" * 3):
            self.assertFalse(graph.notifications_enabled())
        self.assertTrue(graph.notifications_enabled())
        self.client.force_login(self.owner)
        self.assertRedirects(post(self.client, reverse("mail_disconnect")), reverse("owner_mail"))
        self.assertFalse(graph.notifications_enabled())

    def test_backend_retries_401_with_refresh_but_requires_202_acceptance(self):
        with patch("box.graph_backend.access_token", side_effect=["old-token", "fresh-token"]) as token, \
             patch("box.graph_backend.requests.post", side_effect=[Mock(status_code=401), Mock(status_code=202)]) as http:
            self.assertEqual(send_mail("测试", "邮件正文", "unrelated@example.com", ["reader@example.com"]), 1)
        self.assertEqual(token.call_args_list[1].kwargs, {"force_refresh": True})
        kwargs = http.call_args_list[1].kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fresh-token")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"]["message"]["toRecipients"], [{"emailAddress": {"address": "reader@example.com"}}])
        self.assertNotIn("from", kwargs["json"]["message"])
        with patch("box.graph_backend.access_token", return_value="token"), \
             patch("box.graph_backend.requests.post", return_value=Mock(status_code=429)):
            with self.assertRaises(graph.GraphDeliveryError):
                send_mail("测试", "邮件正文", "", ["reader@example.com"])

    def test_automatic_subscription_answer_update_and_unsubscribe_use_graph(self):
        self.connect_cache()
        self.assertEqual(post(self.client, "/ask/", {"text": "私人提问不会出现在邮件里", "email": "reader@example.com"}).status_code, 302)
        question = Question.objects.get()
        sub = question.subscription
        self.assertIsNotNone(sub.subscribed_at)
        with patch("box.graph_backend.access_token", return_value="token"), \
             patch("box.graph_backend.requests.post", return_value=Mock(status_code=202)) as http:
            self.assertTrue(deliver_one())
            self.assertNotIn(question.text, http.call_args.kwargs["json"]["message"]["body"]["content"])
            self.assertIn("已订阅回信", http.call_args.kwargs["json"]["message"]["subject"])
            self.assertNotIn("/subscription/confirm/", http.call_args.kwargs["json"]["message"]["body"]["content"])
            self.client.force_login(self.owner)
            for answer, version in (("第一封回信", 0), ("更新的回信", 1)):
                self.assertEqual(post(self.client, reverse("owner_question", args=[question.pk]),
                                      {"answer": answer, "expected_version": version, "action": "publish"}).status_code, 302)
                self.assertTrue(deliver_one())
            self.assertEqual(http.call_count, 3)
            cancel = reverse("unsubscribe", args=[subscription_token(sub, "unsubscribe")])
            post(self.client, cancel)
            post(self.client, reverse("owner_question", args=[question.pk]),
                 {"answer": "停止订阅之后", "expected_version": 2, "action": "publish"})
            self.assertFalse(deliver_one())
        self.assertEqual(MailJob.objects.filter(status="sent").count(), 3)

    def test_graph_failure_uses_queue_retry_and_does_not_expose_tokens(self):
        self.connect_cache()
        post(self.client, "/ask/", {"text": "发送失败可以重试吗", "email": "reader@example.com"})
        with patch("box.graph_backend.access_token", return_value="private-access-token"), \
             patch("box.graph_backend.requests.post", return_value=Mock(status_code=503)):
            self.assertTrue(deliver_one())
        job = MailJob.objects.get()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.last_error, "GraphDeliveryError")
        self.assertGreater(job.available_at, timezone.now())

    def test_test_mail_requires_connection_and_explicit_post_and_is_limited(self):
        self.client.force_login(self.owner)
        with patch("box.views.send_mail", return_value=1) as send:
            post(self.client, reverse("mail_test"), {"email": "reader@example.com"})
            send.assert_not_called()
            self.connect_cache()
            self.assertEqual(self.client.get(reverse("mail_test")).status_code, 405)
            post(self.client, reverse("mail_test"), {"email": "reader@example.com"})
            post(self.client, reverse("mail_test"), {"email": "reader@example.com"})
            self.assertEqual(send.call_count, 1)

    @override_settings(MS_GRAPH_CLIENT_SECRET="")
    def test_missing_secret_leaves_site_available_and_no_secret_in_owner_page(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertFalse(graph.notifications_enabled())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("owner_mail")), "等待配置客户端密码")
        with patch("box.graph.msal.ConfidentialClientApplication") as app:
            self.assertRedirects(post(self.client, reverse("mail_connect")), reverse("owner_mail"))
            app.assert_not_called()


class OutlookSecretScriptTests(SimpleTestCase):
    def test_secret_prompt_preserves_config_backs_up_and_never_prints_secret(self):
        from scripts.set_outlook_secret import main
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = "SECRET_KEY=original-private-key\nMS_GRAPH_CLIENT_ID=public-id\nMS_GRAPH_CLIENT_SECRET=old-secret\nEMAIL_ENABLED=0\n"
            path.write_text(original)
            out = StringIO()
            with patch("sys.argv", ["set_outlook_secret.py", str(path)]), \
                 patch("sys.stdin.isatty", return_value=True), \
                 patch("getpass.getpass", return_value="new-private-secret"), patch("sys.stdout", out):
                main()
            values = dict(line.split("=", 1) for line in path.read_text().splitlines())
            self.assertEqual(values["SECRET_KEY"], "original-private-key")
            self.assertEqual(values["MS_GRAPH_CLIENT_SECRET"], "new-private-secret")
            self.assertEqual(values["EMAIL_ENABLED"], "1")
            self.assertEqual(values["EMAIL_BACKEND"], graph.BACKEND)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            backup = next(Path(directory).glob(".env-before-outlook-*"))
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("new-private-secret", out.getvalue())

    def test_noninteractive_or_multiline_input_cannot_change_config(self):
        from scripts.set_outlook_secret import main
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SECRET_KEY=original\n")
            for is_tty, secret in ((False, "secret"), (True, "secret\nUNSAFE=1")):
                with patch("sys.argv", ["set_outlook_secret.py", str(path)]), \
                     patch("sys.stdin.isatty", return_value=is_tty), patch("getpass.getpass", return_value=secret):
                    with self.assertRaises(SystemExit):
                        main()
                self.assertEqual(path.read_text(), "SECRET_KEY=original\n")
