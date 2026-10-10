import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.contrib.sessions.models import Session
from django.utils import timezone
from box.mail import deliver_one
from box.models import MailJob, RateBucket, ServerChanJob, BarkJob
from box.serverchan import deliver_one as deliver_notification
from box.bark import deliver_one as deliver_bark


class Command(BaseCommand):
    help = "Deliver queued emails and owner alerts with leases and retries; purge expired rate buckets."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=15)

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                RateBucket.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
                Session.objects.filter(expire_date__lt=timezone.now()).delete()
                MailJob.objects.filter(status__in=["sent", "cancelled"], created_at__lt=timezone.now() - timedelta(days=7)).delete()
                ServerChanJob.objects.filter(status="sent", created_at__lt=timezone.now() - timedelta(days=7)).delete()
                BarkJob.objects.filter(status="sent", created_at__lt=timezone.now() - timedelta(days=7)).delete()
                while True:
                    sent_mail = deliver_one()
                    sent_notification = deliver_notification()
                    sent_bark = deliver_bark()
                    if not sent_mail and not sent_notification and not sent_bark:
                        break
                if options["once"]:
                    return
                time.sleep(max(1, options["interval"]))
        except KeyboardInterrupt:
            return
