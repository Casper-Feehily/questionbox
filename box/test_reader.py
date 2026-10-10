from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Question, Subscription


class ReaderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.letters = [Question.objects.create(text=f"公开问题 {i}", answer=f"完整回信 {i}",
                      answered_at=now, answer_updated_at=now) for i in range(18)]
        cls.pending = Question.objects.create(text="私人问题", answer="尚未发布的内容", draft="私人草稿")
        cls.blank = Question.objects.create(text="空回答不应公开", answered_at=now)
        Subscription.objects.create(question=cls.letters[0], email="private@example.com")

    def test_home_has_reading_entry(self):
        self.assertContains(self.client.get(reverse("home")), f'href="{reverse("reader")}"')

    def test_reader_randomizes_all_public_ids_but_only_loads_three_answers(self):
        with patch("box.reader.secrets.SystemRandom") as random:
            random.return_value.shuffle.side_effect = lambda values: values.reverse()
            response = self.client.get(reverse("reader"))
        random.return_value.shuffle.assert_called_once()
        data = response.context["reader_data"]
        self.assertEqual(len(data["order"]), 18)
        self.assertEqual(len(set(data["order"])), 18)
        self.assertEqual([card["id"] for card in data["cards"]], data["order"][:3])
        self.assertEqual(len(data["cards"]), 3)
        self.assertNotContains(response, "私人问题")
        self.assertNotContains(response, "私人草稿")
        self.assertNotContains(response, "private@example.com")
        self.assertNotIn(str(self.blank.pk), data["order"])

    def test_card_endpoint_preserves_order_and_omits_private_or_deleted_letters(self):
        ids = [self.letters[2].pk, self.pending.pk, uuid4(), self.letters[0].pk, self.blank.pk]
        response = self.client.get(reverse("reader_cards"), {"ids": ",".join(map(str, ids))})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([card["id"] for card in response.json()["cards"]], [str(ids[0]), str(ids[3])])
        self.assertEqual(response.json()["cards"][0]["answer"], self.letters[2].answer)
        self.assertNotContains(response, "draft")
        self.assertNotContains(response, "email")
        self.assertNotContains(response, "尚未发布")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_card_input_is_bounded_and_get_only(self):
        for ids in ["", "bad-id", ",".join(str(uuid4()) for _ in range(7))]:
            self.assertEqual(self.client.get(reverse("reader_cards"), {"ids": ids}).status_code, 400)
        self.assertEqual(self.client.post(reverse("reader_cards"), "a=b", content_type="application/x-www-form-urlencoded").status_code, 405)
        self.assertEqual(self.client.post(reverse("reader"), "a=b", content_type="application/x-www-form-urlencoded").status_code, 405)

    def test_empty_and_single_letter_reader(self):
        Question.objects.all().delete()
        response = self.client.get(reverse("reader"))
        self.assertContains(response, "第一封回信，还在路上。")
        self.assertNotContains(response, 'data-deck')
        Question.objects.create(text="唯一的问题", answer="唯一的回信", answered_at=timezone.now())
        response = self.client.get(reverse("reader"))
        self.assertContains(response, "唯一的回信")
        self.assertEqual(len(response.context["reader_data"]["cards"]), 1)

    def test_letter_markup_is_escaped_in_html_and_json_script(self):
        Question.objects.all().delete()
        Question.objects.create(text='</script><script>alert(1)</script>',
            signature='<b>朋友</b>', answer='<img src=x onerror=alert(2)>', answered_at=timezone.now())
        response = self.client.get(reverse("reader"))
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "\\u003C/script\\u003E")
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, '<img src=x')
        self.assertNotContains(response, '<b>朋友</b>')
