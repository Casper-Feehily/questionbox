from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve


class WebReleaseRoutesTests(SimpleTestCase):
    def test_miniprogram_api_routes_are_published(self):
        self.assertEqual(resolve("/api/v1/meta/").url_name, "api_meta")
        self.assertEqual(resolve("/api/v1/me/questions/").url_name, "api_my_questions")


class WebReleaseBrandingTests(TestCase):
    @override_settings(OWNER_NAME="示例站长", SITE_TITLE="示例站点")
    def test_privacy_uses_configured_owner_in_deletion_policy(self):
        response = self.client.get("/privacy/")
        self.assertContains(response, "示例站长可以删除问题")
