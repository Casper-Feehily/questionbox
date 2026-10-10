import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Create a consistent SQLite online backup without overwriting existing files."

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path)

    def handle(self, *args, **options):
        path = options["output"] or settings.DATA_DIR / "backups" / f"questionbox-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise CommandError("Backup file already exists; choose a new filename") from exc
        connection.ensure_connection()
        with sqlite3.connect(path) as destination:
            connection.connection.backup(destination)
        self.stdout.write(f"Backup saved: {path}")
