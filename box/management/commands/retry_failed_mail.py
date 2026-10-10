from django.core.management.base import BaseCommand
from django.utils import timezone
from box.models import MailJob


class Command(BaseCommand):
    help = "Retry failed email jobs after fixing SMTP; may resend a previously accepted email."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, help="Limit to one job id; omitted means all failed jobs")

    def handle(self, *args, **options):
        jobs = MailJob.objects.filter(status="failed", subscription__active=True)
        if options["job"] is not None:
            jobs = jobs.filter(pk=options["job"])
        count = jobs.update(status="pending", attempts=0, available_at=timezone.now(), lease_until=None, last_error="")
        self.stdout.write(f"Queued {count} failed job(s) for retry.")
