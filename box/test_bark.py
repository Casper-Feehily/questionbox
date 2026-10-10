import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlencode
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from .models import BarkJob, Question
from .bark import BarkError, queue_question, deliver_one, send_notification


@override_settings(BARK_DEVICE_KEY="test-device", BARK_SERVER_URL="https://api.day.app",
                   SERVERCHAN_SENDKEY="", EMAIL_ENABLED=False, QUESTION_RATE_LIMIT_ENABLED=False,
                   SITE_URL="https://ask.example.com")
class BarkTests(TransactionTestCase):
    def ask(self, **fields):
        return self.client.post('/ask/', urlencode({'text': '私人内容', **fields}),
                                content_type='application/x-www-form-urlencoded')

    def test_submission_queues_once_and_worker_sends_private_link(self):
        with patch('box.bark.urlopen') as http:
            self.assertEqual(self.ask(signature='私人署名').status_code, 302)
            queue_question(Question.objects.get())
            http.assert_not_called()
        self.assertEqual(BarkJob.objects.count(), 1)
        with patch('box.bark.send_notification') as send:
            call_command('mail_worker', once=True, stdout=StringIO())
        title, body, url = send.call_args.args
        self.assertNotIn('私人内容', body)
        self.assertNotIn('私人署名', body)
        self.assertTrue(url.startswith('https://ask.example.com/'))
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(BarkJob.objects.get().status, 'sent')
        self.assertFalse(deliver_one())

    @override_settings(BARK_DEVICE_KEY='')
    def test_disabled(self):
        self.assertEqual(self.ask().status_code, 302)
        self.assertFalse(BarkJob.objects.exists())
        self.assertFalse(deliver_one())

    def test_invalid_and_honeypot_do_not_queue(self):
        self.assertEqual(self.ask(text='问' * 501).status_code, 400)
        self.assertEqual(self.ask(website='spam').status_code, 302)
        self.assertFalse(BarkJob.objects.exists())

    def test_transaction_rolls_back(self):
        with patch('box.submission.queue_bark_question', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.ask()
        self.assertFalse(Question.objects.exists())
        self.assertFalse(BarkJob.objects.exists())

    def test_json_api_and_response_validation(self):
        with patch('box.bark.urlopen') as http:
            http.return_value.__enter__.return_value.read.return_value = b'{"code":200}'
            send_notification('中文标题', '内容', 'https://ask.example.com/owner/')
        request = http.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.day.app/push')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data)['device_key'], 'test-device')
        self.assertEqual(http.call_args.kwargs['timeout'], 10)
        for data in [b'[]', b'{}', b'{"code":true}', b'{"code":"200"}', b'{"code":400}']:
            with self.subTest(data=data), patch('box.bark.urlopen') as http:
                http.return_value.__enter__.return_value.read.return_value = data
                with self.assertRaises(BarkError):
                    send_notification('标题', '内容', 'https://ask.example.com')

    def test_retry_limit_redaction_and_manual_retry(self):
        self.ask()
        with patch('box.bark.urlopen', side_effect=URLError('secret device key')):
            for attempt in range(1, 6):
                BarkJob.objects.update(available_at=timezone.now() - timedelta(seconds=1))
                self.assertTrue(deliver_one())
                job = BarkJob.objects.get()
                self.assertEqual(job.attempts, attempt)
                self.assertEqual(job.last_error, 'URLError')
                self.assertEqual(job.status, 'failed' if attempt == 5 else 'pending')
        call_command('retry_failed_bark', stdout=StringIO())
        with patch('box.bark.send_notification'):
            self.assertTrue(deliver_one())
        self.assertEqual(BarkJob.objects.get().status, 'sent')

    def test_leases_and_deletion(self):
        self.ask()
        BarkJob.objects.update(status='sending', lease_until=timezone.now() + timedelta(minutes=3))
        self.assertFalse(deliver_one())
        BarkJob.objects.update(lease_until=timezone.now() - timedelta(seconds=1))
        with patch('box.bark.send_notification'):
            self.assertTrue(deliver_one())
        Question.objects.get().delete()
        self.assertFalse(BarkJob.objects.exists())

    def test_test_command_does_not_create_question(self):
        with patch('box.management.commands.test_bark.send_notification') as send:
            call_command('test_bark', stdout=StringIO())
            send.assert_called_once()
        self.assertFalse(Question.objects.exists())
