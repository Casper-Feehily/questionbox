import math
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from .models import RateBucket


class RateLimited(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, math.ceil(seconds))


def digest(scope, value):
    return salted_hmac(f"questionbox.rate.{scope}", value, algorithm="sha256").hexdigest()


@transaction.atomic
def consume(limits):
    """Atomically consume every independent budget, or none. SQLite uses BEGIN IMMEDIATE."""
    now = timezone.now()
    buckets = []
    for scope, identity, maximum, seconds in limits:
        key = digest(scope, identity)
        bucket, _ = RateBucket.objects.select_for_update().get_or_create(
            key=key, defaults={"count": 0, "expires_at": now + timedelta(seconds=seconds)}
        )
        if bucket.expires_at <= now:
            bucket.count, bucket.expires_at = 0, now + timedelta(seconds=seconds)
        if bucket.count >= maximum:
            raise RateLimited((bucket.expires_at - now).total_seconds())
        buckets.append(bucket)
    for bucket in buckets:
        bucket.count += 1
        bucket.save(update_fields=["count", "expires_at"])
