"""Private owner alerts delivered by the existing background worker."""
import json
from datetime import timedelta
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import BarkJob


class BarkError(Exception):
    """Only fixed messages and numeric API codes, never credentials or response text."""


def send_notification(title, body, url):
    endpoint = settings.BARK_SERVER_URL + "/push"
    payload = json.dumps({"device_key": settings.BARK_DEVICE_KEY, "title": title,
                          "body": body, "url": url, "group": "questionbox"},
                         ensure_ascii=False).encode("utf-8")
    request = Request(endpoint, data=payload,
                      headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read(65536))
    if not isinstance(result, dict) or type(result.get("code")) is not int:
        raise BarkError("InvalidResponse")
    if result["code"] != 200:
        raise BarkError(f"APICode:{result['code']}")


def queue_question(question):
    if settings.BARK_DEVICE_KEY:
        BarkJob.objects.get_or_create(question=question)


def deliver_one():
    if not settings.BARK_DEVICE_KEY:
        return False
    now = timezone.now()
    with transaction.atomic():
        job = BarkJob.objects.select_for_update().filter(
            Q(status="pending", available_at__lte=now) | Q(status="sending", lease_until__lte=now)
        ).order_by("created_at").first()
        if not job:
            return False
        job.status, job.lease_until = "sending", now + timedelta(minutes=3)
        job.attempts += 1
        job.save(update_fields=["status", "lease_until", "attempts"])
        job_id = job.pk
    try:
        job = BarkJob.objects.select_related("question").get(pk=job_id)
        url = settings.SITE_URL + reverse("owner_question", args=[job.question_id])
        # Keep visitor content and identifying information out of push notifications.
        send_notification("提问箱收到新问题", "你的提问箱收到了一条新提问。点击查看，需主人登录。", url)
        BarkJob.objects.filter(pk=job_id).update(status="sent", lease_until=None, last_error="")
    except BarkJob.DoesNotExist:
        pass  # The owner deleted the question while the job was leased.
    except Exception as exc:
        error = str(exc) if isinstance(exc, BarkError) else type(exc).__name__
        BarkJob.objects.filter(pk=job_id).update(
            status="failed" if job.attempts >= 5 else "pending", lease_until=None,
            available_at=timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** job.attempts)),
            last_error=error[:80],
        )
    return True
