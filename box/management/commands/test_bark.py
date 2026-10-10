from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from box.bark import BarkError, send_notification


class Command(BaseCommand):
    help = "Send a Bark test notification without creating a question."

    def handle(self, *args, **options):
        if not settings.BARK_DEVICE_KEY:
            raise CommandError("Set BARK_DEVICE_KEY in .env first.")
        try:
            send_notification("提问箱提醒已连接", "这是一条测试通知。有人提问时会通知你。",
                              settings.SITE_URL + reverse("owner"))
        except Exception as exc:
            error = str(exc) if isinstance(exc, BarkError) else type(exc).__name__
            raise CommandError(f"Bark test failed: {error}") from None
        self.stdout.write(self.style.SUCCESS("Bark accepted the test notification."))
