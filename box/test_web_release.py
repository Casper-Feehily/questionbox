from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, resolve


class WebReleaseRoutesTests(SimpleTestCase):
    def test_private_client_api_is_not_published(self):
        for path in ("/api/v1/meta/", "/api/v1/me/questions/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)


class WebReleaseBrandingTests(TestCase):
    @override_settings(OWNER_NAME="示例站长", SITE_TITLE="示例站点")
    def test_privacy_uses_configured_owner_in_deletion_policy(self):
        response = self.client.get("/privacy/")
        self.assertContains(response, "示例站长可以删除问题")
