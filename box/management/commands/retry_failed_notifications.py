from django.core.management.base import BaseCommand
from django.utils import timezone

from box.models import ServerChanJob


class Command(BaseCommand):
    help = "Retry failed ServerChan owner alerts after fixing configuration or quota."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, help="Limit to one job id; omitted means all failed jobs")

    def handle(self, *args, **options):
        jobs = ServerChanJob.objects.filter(status="failed")
        if options["job"] is not None:
            jobs = jobs.filter(pk=options["job"])
        count = jobs.update(status="pending", attempts=0, available_at=timezone.now(), lease_until=None, last_error="")
        self.stdout.write(f"Queued {count} failed owner alert(s) for retry.")
