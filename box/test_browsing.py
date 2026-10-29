from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import MailJob, Question, Subscription, WechatMessageJob


def post(client, url, data):
    return client.post(url, urlencode(data or {"_form": "1"}, doseq=True), content_type="application/x-www-form-urlencoded", HTTP_ACCEPT="application/json")


class BrowsingTests(TestCase):
    def letter(self, text="公开问题", **kwargs):
        return Question.objects.create(text=text, answer="认真回答。", answered_at=timezone.now(),
                                       answer_updated_at=timezone.now(), **kwargs)

    def tag(self, name):
        from .models import Tag
        return Tag.objects.create(name=name)

    def test_home_is_for_browsing_and_ask_has_its_own_page(self):
        self.letter()
        home = self.client.get(reverse("home"))
        self.assertContains(home, 'href="/ask/"')
        self.assertNotContains(home, 'name="text"')
        ask = self.client.get(reverse("ask"))
        self.assertEqual(ask.status_code, 200)
        self.assertContains(ask, 'name="text"')
        self.assertNotContains(ask, 'data-answer-wall')

    def test_default_featured_shows_untagged_featured_answers_without_filters(self):
        self.letter(is_featured=True)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["selected_view"], "featured")
        self.assertContains(response, "公开问题")
        self.assertNotContains(response, 'class="tag-filters"')
        self.assertContains(self.client.get(reverse("home"), {"view": "all"}), "公开问题")

    def test_home_statistics_count_only_public_answers_and_topics(self):
        public_tag, private_tag = self.tag("公开话题"), self.tag("私密话题")
        self.letter("第一封", is_featured=True).tags.add(public_tag)
        self.letter("第二封").tags.add(public_tag)
        Question.objects.create(text="尚未公开").tags.add(private_tag)
        response = self.client.get(reverse("home"), {"view": "featured", "tag": public_tag.pk})
        self.assertEqual(response.context.get("public_answer_count"), 2)
        self.assertEqual(response.context.get("public_topic_count"), 1)
        self.assertNotContains(response, "私密话题")

    def test_featured_lists_only_public_featured_answers_across_all_tags(self):
        study, daily, private = (self.tag(name) for name in ["学习", "日常", "私人话题"])
        featured = self.letter("精选问题", is_featured=True)
        featured.tags.add(study)
        ordinary = self.letter("普通问题")
        ordinary.tags.add(study, daily)
        other_featured = self.letter("其他标签的精选问题", is_featured=True)
        other_featured.tags.add(daily)
        untagged = self.letter("未添加标签的精选问题", is_featured=True)
        Question.objects.create(text="未公开问题", is_featured=True).tags.add(study, private)
        Question.objects.create(text="空回答", is_featured=True, answered_at=timezone.now()).tags.add(daily, private)
        default = self.client.get(reverse("home"), {"view": "featured", "tag": study.pk})
        self.assertEqual(default.context["selected_view"], "featured")
        self.assertEqual(set(default.context["page"]), {featured, other_featured, untagged})
        self.assertEqual(default.context["answered_count"], 3)
        self.assertNotContains(default, 'class="category-card"')
        self.assertNotContains(default, '&amp;tag=')
        self.assertNotContains(default, 'class="tag-filters"')
        self.assertContains(default, 'class="answer-card"', count=3)
        self.assertContains(default, "精选问题")
        self.assertNotContains(default, "普通问题")
        self.assertNotContains(default, "私人话题")
        self.assertNotContains(default, "未公开问题")
        self.assertNotContains(default, "空回答")
        self.assertContains(self.client.get(reverse("home"), {"view": "all"}), "普通问题")

    def test_tag_filter_only_in_all_answers_and_hides_private_tags(self):
        public, private, ordinary = self.tag("学习"), self.tag("私人分类"), self.tag("日常")
        selected = self.letter("精选学习", is_featured=True)
        selected.tags.add(public)
        other = self.letter("普通日常")
        other.tags.add(ordinary)
        pending = Question.objects.create(text="未公开", is_featured=True)
        pending.tags.add(private)
        response = self.client.get(reverse("home"), {"view": "all", "tag": public.pk})
        self.assertContains(response, "精选学习")
        self.assertNotContains(response, "普通日常")
        self.assertNotContains(response, "私人分类")
        self.assertContains(response, 'class="tag-filters"')
        self.assertContains(response, "日常")
        self.assertNotContains(response, 'href="/read/?')

        featured = self.client.get(reverse("home"), {"view": "featured", "tag": public.pk})
        self.assertEqual(set(featured.context["page"]), {selected})
        self.assertFalse(featured.context["has_tag_filter"])
        self.assertEqual(featured.context["tag_param"], "")
        self.assertContains(featured, 'href="?view=featured&amp;per_page=14#answers-title"')
        self.assertNotContains(featured, 'class="tag-filters"')
        self.assertNotContains(featured, 'class="card-tags"')

    def test_pagination_retains_view_and_tag(self):
        self.assertTrue(hasattr(Question, "tags"))
        tag = self.tag("标签 & 特殊")
        for index in range(16):
            self.letter(f"问答 {index}").tags.add(tag)
        response = self.client.get(reverse("home"), {"view": "all", "tag": tag.pk, "per_page": 7, "page": 2})
        self.assertEqual(len(response.context["page"]), 7)
        self.assertContains(response, f"view=all&amp;tag={tag.pk}&amp;page=3&amp;per_page=7")

    def test_invalid_and_private_tag_filters_show_no_content(self):
        self.assertTrue(hasattr(Question, "tags"))
        private = self.tag("秘密标签")
        Question.objects.create(text="私人问题").tags.add(private)
        self.letter("不匹配的公开问题")
        for value in ["invalid", str(private.pk), "999999999999999999999999999999"]:
            response = self.client.get(reverse("home"), {"tag": value})
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "不匹配的公开问题")
            self.assertNotContains(response, "秘密标签")
            self.assertContains(response, "清除筛选")

    def test_previous_category_link_opens_unfiltered_featured_and_tag_only_opens_all(self):
        tag = self.tag("只有普通问答的标签")
        self.letter("普通问答").tags.add(tag)
        featured = self.client.get(reverse("home"), {"view": "categories", "tag": tag.pk})
        self.assertEqual(featured.context["selected_view"], "featured")
        self.assertFalse(featured.context["has_tag_filter"])
        self.assertNotContains(featured, "普通问答")
        restored = self.client.get(reverse("home"), {"tag": tag.pk})
        self.assertEqual(restored.context["selected_view"], "all")
        self.assertContains(restored, "普通问答")

    def test_featured_pagination_ignores_tag_and_excludes_ordinary_answers(self):
        tag = self.tag("仅一个问题的标签")
        questions = [self.letter(f"精选问答 {i}", is_featured=True) for i in range(16)]
        self.letter("普通公开问答")
        questions[0].tags.add(tag)
        response = self.client.get(reverse("home"),
                                   {"view": "featured", "tag": tag.pk, "per_page": 7, "page": 2})
        self.assertEqual(response.context["answered_count"], 16)
        self.assertEqual(len(response.context["page"]), 7)
        self.assertContains(response, 'view=featured&amp;page=3&amp;per_page=7')
        self.assertNotContains(response, '&amp;tag=')

    def test_empty_featured_does_not_fall_back_to_ordinary_answers(self):
        self.letter("只有普通回答")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["selected_view"], "featured")
        self.assertEqual(response.context["answered_count"], 0)
        self.assertNotContains(response, "只有普通回答")
        self.assertContains(response, "精选回信，正在整理。")
        self.assertNotContains(response, 'class="tag-filters"')
        self.assertContains(self.client.get(reverse("home"), {"view": "all"}), "只有普通回答")

    @override_settings(QUESTION_RATE_LIMIT_ENABLED=False)
    def test_ask_errors_preserve_input_on_separate_page(self):
        response = post(self.client, reverse("ask"), {"text": "问" * 701, "signature": "小明"})
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "ask.html")
        self.assertContains(response, "小明", status_code=400)
        self.assertNotContains(response, 'data-answer-wall', status_code=400)
        success = post(self.client, reverse("ask"), {"text": "新问题"})
        self.assertRedirects(success, reverse("thanks"))
        self.assertContains(self.client.get(reverse("thanks")), 'href="/ask/"')

    @override_settings(MAX_QUESTIONS=1, QUESTION_RATE_LIMIT_ENABLED=False)
    def test_full_box_is_reported_on_ask_page(self):
        Question.objects.create(text="已有问题")
        self.assertContains(self.client.get(reverse("ask")), "提问箱已满")
        response = post(self.client, reverse("ask"), {"text": "新问题"})
        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "ask.html")

    def test_reader_starts_with_all_and_can_filter_tags(self):
        self.assertTrue(hasattr(Question, "tags"))
        tag = self.tag("学习")
        selected = self.letter("有标签的公开问题", is_featured=True)
        selected.tags.add(tag)
        self.letter("非精选公开问题")
        pending = Question.objects.create(text="私人问题")
        pending.tags.add(tag)
        all_letters = self.client.get(reverse("reader"), {"view": "featured"})
        self.assertEqual(all_letters.context["total"], 2)
        response = self.client.get(reverse("reader"), {"tag": tag.pk})
        self.assertEqual(response.context["reader_data"]["order"], [str(selected.pk)])
        self.assertContains(response, f'/read/cards/?tag={tag.pk}')
        ids = ",".join(str(q.pk) for q in Question.objects.all())
        cards = self.client.get(reverse("reader_cards"), {"tag": tag.pk, "ids": ids})
        self.assertEqual([card["id"] for card in cards.json()["cards"]], [str(selected.pk)])
        self.assertContains(self.client.get(reverse("reader"), {"tag": "bad"}), "清除筛选")


class ClassificationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(username="owner", password="test-owner-password")
        self.question = Question.objects.create(text="问题", answer="已公开的回答", draft="正在编辑的草稿",
                                               answered_at=timezone.now(), answer_updated_at=timezone.now(), version=2)
        self.url = f"/owner/q/{self.question.pk}/classification/"

    def test_owner_can_classify_without_changing_answer_or_notifying(self):
        self.client.force_login(self.owner)
        before = (self.question.answer, self.question.draft, self.question.version, self.question.answer_updated_at)
        response = post(self.client, self.url, {"is_featured": "on", "new_tags": "学习, 生活，学习"})
        self.assertEqual(response.status_code, 200)
        self.question.refresh_from_db()
        self.assertTrue(self.question.is_featured)
        self.assertEqual(set(self.question.tags.values_list("name", flat=True)), {"学习", "生活"})
        self.assertEqual((self.question.answer, self.question.draft, self.question.version,
                          self.question.answer_updated_at), before)
        self.assertEqual(MailJob.objects.count(), 0)
        self.assertEqual(WechatMessageJob.objects.count(), 0)
        self.assertEqual(post(self.client, self.url, {"tags": [self.question.tags.first().pk]}).status_code, 200)
        self.question.refresh_from_db()
        self.assertFalse(self.question.is_featured)
        self.assertEqual(self.question.tags.count(), 1)

    def test_classification_requires_owner_and_csrf(self):
        self.assertEqual(post(self.client, self.url, {}).status_code, 302)
        member = get_user_model().objects.create_user(username="member")
        self.client.force_login(member)
        self.assertEqual(post(self.client, self.url, {}).status_code, 404)
        from django.test import Client
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.owner)
        self.assertEqual(post(secured, self.url, {}).status_code, 403)

    def test_invalid_classification_is_atomic(self):
        self.client.force_login(self.owner)
        response = post(self.client, self.url, {"is_featured": "on", "new_tags": "x" * 31})
        self.assertEqual(response.status_code, 400)
        self.question.refresh_from_db()
        self.assertFalse(self.question.is_featured)
        self.assertEqual(self.question.tags.count(), 0)

    def test_native_classification_submission_uses_html(self):
        self.client.force_login(self.owner)
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8"
        response = self.client.post(self.url, "is_featured=on", content_type="application/x-www-form-urlencoded", HTTP_ACCEPT=accept)
        self.assertRedirects(response, reverse("owner_question", args=[self.question.pk]))
        invalid = self.client.post(self.url, urlencode({"new_tags": "x" * 31}), content_type="application/x-www-form-urlencoded", HTTP_ACCEPT=accept)
        self.assertEqual(invalid.status_code, 400)
        self.assertTemplateUsed(invalid, "owner_question.html")
        self.assertContains(invalid, "每个标签最多 30 字", status_code=400)
        self.assertEqual(invalid.context["form"]["answer"].value(), self.question.draft)
