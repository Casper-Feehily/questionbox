"""Private owner alerts delivered by the existing background worker."""
import json
import re
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import ServerChanJob


class ServerChanError(Exception):
    """Only fixed messages and numeric API codes, never credentials or response text."""


def send_notification(title, description):
    key = settings.SERVERCHAN_SENDKEY
    if re.fullmatch(r"SCT[A-Za-z0-9]+", key):
        endpoint = f"https://sctapi.ftqq.com/{key}.send"
    else:
        match = re.fullmatch(r"sctp([0-9]+)t[A-Za-z0-9]+", key)
        if not match:
            raise ServerChanError("InvalidSendKey")
        endpoint = f"https://{match[1]}.push.ft07.com/send/{key}.send"
    payload = urlencode({"title": title[:32], "desp": description, "noip": 1}).encode("utf-8")
    request = Request(endpoint, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read(65536))
    if not isinstance(result, dict) or type(result.get("code")) is not int:
        raise ServerChanError("InvalidResponse")
    if result["code"] != 0:
        raise ServerChanError(f"APICode:{result['code']}")


def queue_question(question):
    if settings.SERVERCHAN_SENDKEY:
        ServerChanJob.objects.get_or_create(question=question)


def deliver_one():
    if not settings.SERVERCHAN_SENDKEY:
        return False
    now = timezone.now()
    with transaction.atomic():
        job = ServerChanJob.objects.select_for_update().filter(
            Q(status="pending", available_at__lte=now) | Q(status="sending", lease_until__lte=now)
        ).order_by("created_at").first()
        if not job:
            return False
        job.status, job.lease_until = "sending", now + timedelta(minutes=3)
        job.attempts += 1
        job.save(update_fields=["status", "lease_until", "attempts"])
        job_id = job.pk
    try:
        job = ServerChanJob.objects.select_related("question").get(pk=job_id)
        url = settings.SITE_URL + reverse("owner_question", args=[job.question_id])
        description = f"你的提问箱收到了一条新提问。\n\n[打开私人收件箱查看]({url})\n\n查看需要主人登录。"
        # Never send question text, signature, visitor email, or visitor IP to the provider.
        send_notification("提问箱收到新问题", description)
        ServerChanJob.objects.filter(pk=job_id).update(status="sent", lease_until=None, last_error="")
    except ServerChanJob.DoesNotExist:
        pass  # The owner deleted the question while the job was leased.
    except Exception as exc:
        error = str(exc) if isinstance(exc, ServerChanError) else type(exc).__name__
        ServerChanJob.objects.filter(pk=job_id).update(
            status="failed" if job.attempts >= 5 else "pending", lease_until=None,
            available_at=timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** job.attempts)),
            last_error=error[:80],
        )
    return True
