"""Server-only WeChat integration. Provider payloads and credentials are never logged."""
import hashlib
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from .models import WechatIdentity, WechatSession, WechatReminder, WechatMessageJob


class WechatError(Exception):
    def __init__(self, code="InvalidResponse"):
        self.code = code
        super().__init__(str(code))


class DeliveryUncertain(WechatError):
    pass


def reminders_enabled():
    return bool(settings.WECHAT_ENABLED and settings.WECHAT_APPID and settings.WECHAT_APP_SECRET
                and settings.WECHAT_REPLY_TEMPLATE_ID and settings.WECHAT_REPLY_TEMPLATE_FIELDS)


def checked_result(response):
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise WechatError()
    code = result.get("errcode", 0)
    if type(code) is not int:
        raise WechatError()
    if code:
        raise WechatError(code)
    return result


def exchange_code(code):
    result = checked_result(requests.get("https://api.weixin.qq.com/sns/jscode2session", params={
        "appid": settings.WECHAT_APPID, "secret": settings.WECHAT_APP_SECRET,
        "js_code": code, "grant_type": "authorization_code",
    }, timeout=10))
    openid = result.get("openid")
    if not isinstance(openid, str) or not 1 <= len(openid) <= 128:
        raise WechatError()
    return openid


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@transaction.atomic
def create_session(openid):
    identity, _ = WechatIdentity.objects.get_or_create(appid=settings.WECHAT_APPID, openid=openid)
    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(days=settings.WECHAT_SESSION_DAYS)
    WechatSession.objects.create(token_digest=token_digest(token), identity=identity, expires_at=expires_at)
    return token, expires_at


def access_cache_key():
    value = settings.WECHAT_APPID + ":" + settings.WECHAT_APP_SECRET
    return "wechat.access." + hashlib.sha256(value.encode()).hexdigest()


def access_token():
    key = access_cache_key()
    token = cache.get(key)
    if token:
        return token
    result = checked_result(requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential", "appid": settings.WECHAT_APPID,
        "secret": settings.WECHAT_APP_SECRET,
    }, timeout=10))
    token, expires = result.get("access_token"), result.get("expires_in")
    if not isinstance(token, str) or not token or type(expires) is not int or expires <= 120:
        raise WechatError()
    cache.set(key, token, expires - 120)
    return token


def send_reply(question):
    token = access_token()  # Failure before sending is safe to retry.
    now = timezone.localtime(question.answered_at)
    values = {"notice": "你有一封新回信", "status": "已回复", "time": now.strftime("%Y-%m-%d %H:%M"),
              "date": now.strftime("%Y-%m-%d")}
    payload = {
        "touser": question.wechat_identity.openid,
        "template_id": settings.WECHAT_REPLY_TEMPLATE_ID,
        "page": f"pages/detail/index?id={question.pk}&mine=1",
        "miniprogram_state": settings.WECHAT_MINIPROGRAM_STATE,
        "lang": "zh_CN",
        "data": {key: {"value": values[value]} for key, value in settings.WECHAT_REPLY_TEMPLATE_FIELDS.items()},
    }
    try:
        result = checked_result(requests.post("https://api.weixin.qq.com/cgi-bin/message/subscribe/send",
                                             params={"access_token": token}, json=payload, timeout=10))
        # Unlike login/token responses, successful sends must explicitly acknowledge success.
        if result.get("errcode") != 0 or type(result.get("errcode")) is not int:
            raise DeliveryUncertain("InvalidSendResponse")
    except WechatError as exc:
        if exc.code == "InvalidResponse":
            raise DeliveryUncertain("InvalidSendResponse") from None
        if exc.code in {40001, 40014, 42001}:
            cache.delete(access_cache_key())
        raise
    except (requests.RequestException, ValueError):
        raise DeliveryUncertain("SendResultUnknown") from None


def queue_reply(question):
    if not reminders_enabled() or not question.wechat_identity_id or not question.answered_at or not question.answer:
        return
    try:
        reminder = question.wechat_reminder
    except WechatReminder.DoesNotExist:
        return
    if reminder.active:
        job, created = WechatMessageJob.objects.get_or_create(reminder=reminder)
        # A cancelled, never-attempted task can be re-enabled. Keep completed/uncertain sentinels.
        if not created and job.status == "cancelled" and job.attempts == 0:
            WechatMessageJob.objects.filter(pk=job.pk).update(status="pending", available_at=timezone.now())


def retry_failed(job_id=None, include_uncertain=False):
    jobs = WechatMessageJob.objects.filter(status__in=["failed", "uncertain"] if include_uncertain else ["failed"],
                                           reminder__active=True, reminder__question__answered_at__isnull=False,
                                           reminder__question__wechat_identity__appid=settings.WECHAT_APPID)
    if job_id is not None:
        jobs = jobs.filter(pk=job_id)
    return jobs.update(status="pending", attempts=0, available_at=timezone.now(), lease_until=None, last_error="")


def deliver_one():
    if not reminders_enabled():
        return False
    now = timezone.now()
    with transaction.atomic():
        # A crashed sender may already have reached WeChat. Never silently resend its lease.
        WechatMessageJob.objects.filter(status="sending", lease_until__lte=now).update(
            status="uncertain", lease_until=None, last_error="ExpiredSendingLease")
        job = WechatMessageJob.objects.select_for_update().filter(
            status="pending", available_at__lte=now).order_by("created_at").first()
        if not job:
            return False
        job.status, job.lease_until = "sending", now + timedelta(minutes=3)
        job.attempts += 1
        job.save(update_fields=["status", "lease_until", "attempts"])
        job_id, attempts = job.pk, job.attempts
    try:
        job = WechatMessageJob.objects.select_related("reminder__question__wechat_identity").get(pk=job_id)
        question = job.reminder.question
        if (job.status != "sending" or not job.reminder.active or not question.answered_at or not question.answer
                or not question.wechat_identity_id or question.wechat_identity.appid != settings.WECHAT_APPID):
            WechatMessageJob.objects.filter(pk=job_id, status="sending").update(status="cancelled", lease_until=None)
            return True
        send_reply(question)
        WechatMessageJob.objects.filter(pk=job_id, status="sending").update(status="sent", lease_until=None, last_error="")
    except WechatMessageJob.DoesNotExist:
        pass
    except Exception as exc:
        error = f"APICode:{exc.code}" if isinstance(exc, WechatError) else type(exc).__name__
        if isinstance(exc, DeliveryUncertain):
            status = "uncertain"
        elif isinstance(exc, WechatError) and type(exc.code) is int and exc.code not in {-1, 40001, 40014, 42001, 45009}:
            status = "rejected"  # e.g. 43101: no subscription permission; retry cannot fix it.
        else:
            status = "failed" if attempts >= 5 else "pending"
        WechatMessageJob.objects.filter(pk=job_id, status="sending").update(
            status=status, lease_until=None, last_error=error[:80],
            available_at=timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** attempts)))
    return True
