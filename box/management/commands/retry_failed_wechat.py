from django.core.management.base import BaseCommand, CommandError
from box.wechat import retry_failed


class Command(BaseCommand):
    help = "Retry failed WeChat reminders; uncertain delivery requires an explicit single-job override."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int)
        parser.add_argument("--include-uncertain", action="store_true",
                            help="After investigating one job, allow a resend that may duplicate a delivered reminder")

    def handle(self, *args, **options):
        if options["include_uncertain"] and options["job"] is None:
            raise CommandError("--include-uncertain requires --job to avoid a bulk ambiguous resend")
        count = retry_failed(options["job"], options["include_uncertain"])
        self.stdout.write(f"Queued {count} WeChat reminder(s) for retry.")
