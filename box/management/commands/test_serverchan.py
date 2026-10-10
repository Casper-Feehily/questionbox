from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from box.serverchan import ServerChanError, send_notification


class Command(BaseCommand):
    help = "Send one test owner alert using SERVERCHAN_SENDKEY; no question is created."

    def handle(self, *args, **options):
        if not settings.SERVERCHAN_SENDKEY:
            raise CommandError("Set SERVERCHAN_SENDKEY in the server's .env first.")
        url = settings.SITE_URL + reverse("owner")
        try:
            send_notification("提问箱提醒已连接", f"这是一条测试通知。有人提交新提问时，你会收到提醒。\n\n[打开私人收件箱]({url})")
        except Exception as exc:
            error = str(exc) if isinstance(exc, ServerChanError) else type(exc).__name__
            raise CommandError(f"ServerChan test failed: {error}") from None
        self.stdout.write(self.style.SUCCESS("ServerChan accepted the test notification. Check your configured receiving channel."))
