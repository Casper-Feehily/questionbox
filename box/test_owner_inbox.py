from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Question, Tag


class OwnerInboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser(
            username="inbox-owner", password="private-test-password-123!"
        )

    def setUp(self):
        self.client.force_login(self.owner)

    def test_untagged_includes_pending_and_published_but_excludes_tagged(self):
        pending = Question.objects.create(text="未分类的待回答问题")
        published = Question.objects.create(
            text="未分类的公开问题", answer="回答", answered_at=timezone.now()
        )
        tagged = Question.objects.create(text="已有两个标签")
        tagged.tags.add(Tag.objects.create(name="日常"), Tag.objects.create(name="学习"))

        response = self.client.get(reverse("owner"), {"state": "untagged"})

        self.assertEqual(response.context["selected"], "untagged")
        self.assertEqual(response.context["untagged_count"], 2)
        self.assertEqual(set(response.context["page"]), {pending, published})
        self.assertContains(response, 'href="?state=untagged" aria-current="page"')
        self.assertContains(response, "未添加标签 <span>2</span>")
        self.assertNotContains(response, tagged.text)

    def test_untagged_pagination_keeps_filter_and_excludes_tagged(self):
        Question.objects.bulk_create([Question(text=f"待分类问题 {i}") for i in range(21)])
        tagged = Question.objects.create(text="已分类问题")
        tagged.tags.add(Tag.objects.create(name="日常"))

        first = self.client.get(reverse("owner"), {"state": "untagged"})
        second = self.client.get(reverse("owner"), {"state": "untagged", "page": 2})

        self.assertEqual(first.context["page"].paginator.count, 21)
        self.assertContains(first, "?state=untagged&amp;page=2")
        self.assertEqual(len(second.context["page"]), 1)
        self.assertNotContains(second, tagged.text)

    def test_saving_tags_removes_question_and_clearing_tags_restores_it(self):
        question = Question.objects.create(text="等待添加标签")
        url = reverse("owner_classification", args=[question.pk])
        response = self.client.post(url, urlencode({"new_tags": "日常"}),
                                    content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("owner"), {"state": "untagged"})
        self.assertEqual(response.context.get("untagged_count"), 0)
        self.assertNotContains(response, question.text)
        self.assertContains(response, "所有问题都已添加标签。")

        self.assertEqual(self.client.post(url, "_form=1",
                                         content_type="application/x-www-form-urlencoded").status_code, 302)
        response = self.client.get(reverse("owner"), {"state": "untagged"})
        self.assertEqual(response.context["untagged_count"], 1)
        self.assertContains(response, question.text)

    def test_existing_states_and_unknown_state_keep_publication_filter(self):
        pending = Question.objects.create(text="待回答问题")
        published = Question.objects.create(text="公开问题", answer="回答", answered_at=timezone.now())
        for state, selected, expected in [
            ("pending", "pending", pending),
            ("answered", "answered", published),
            ("invalid", "pending", pending),
        ]:
            with self.subTest(state=state):
                response = self.client.get(reverse("owner"), {"state": state})
                self.assertEqual(response.context["selected"], selected)
                self.assertEqual(list(response.context["page"]), [expected])
                self.assertEqual(response.context.get("untagged_count"), 2)

    def test_untagged_requires_owner(self):
        self.client.logout()
        self.assertRedirects(
            self.client.get(reverse("owner"), {"state": "untagged"}),
            "/owner/login/?next=/owner/",
        )
        ordinary = get_user_model().objects.create_user(username="ordinary")
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(reverse("owner"), {"state": "untagged"}).status_code, 404)
