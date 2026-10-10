from datetime import timedelta
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from .models import MailJob, Subscription


def subscription_token(subscription, purpose):
    return signing.dumps(str(subscription.pk), salt=f"questionbox.{purpose}")


def get_subscription(token, purpose):
    pk = signing.loads(token, salt=f"questionbox.{purpose}", max_age=172800 if purpose == "confirm" else None)
    return Subscription.objects.select_related("question").get(pk=pk)


def queue_answer(question, event):
    if not settings.EMAIL_ENABLED:
        return
    try:
        sub = question.subscription
    except Subscription.DoesNotExist:
        return
    if sub.active and sub.subscribed_at:
        MailJob.objects.get_or_create(subscription=sub, event=event, version=question.version)


def deliver_one():
    if not settings.EMAIL_ENABLED:
        return False
    now = timezone.now()
    with transaction.atomic():
        job = MailJob.objects.select_for_update().filter(
            Q(status="pending", available_at__lte=now) | Q(status="sending", lease_until__lte=now)
        ).order_by("created_at").first()
        if not job:
            return False
        job.status, job.lease_until = "sending", now + timedelta(minutes=3)
        job.attempts += 1
        job.save(update_fields=["status", "lease_until", "attempts"])
        job_id = job.pk
    try:
        job = MailJob.objects.select_related("subscription__question").get(pk=job_id)
        sub, question = job.subscription, job.subscription.question
        obsolete = (not sub.active
                    or (job.event == "confirm" and sub.subscribed_at)
                    or (job.event == "subscribed" and not sub.subscribed_at)
                    or (job.event in {"answered", "updated"} and (not sub.subscribed_at or not question.answered_at))
                    or job.event not in {"confirm", "subscribed", "answered", "updated"})
        if obsolete:
            MailJob.objects.filter(pk=job_id).update(status="cancelled", lease_until=None)
            return True
        cancel_url = settings.SITE_URL + reverse("unsubscribe", args=[subscription_token(sub, "unsubscribe")])
        if job.event == "subscribed":
            subject = f"已订阅回信 · {settings.SITE_TITLE}"
            text = f"你的便签已收到，这条问题的回答和更新通知已自动订阅。\n\n{settings.OWNER_NAME} 发布回答或更新后，我们会给你发邮件。无需再次确认。\n\n停止这条问题的通知：\n{cancel_url}\n\n如果不是你填写的邮箱，也可以通过上面的链接停止通知。为保护隐私，邮件不包含提问原文。"
        elif job.event == "confirm":
            url = settings.SITE_URL + reverse("confirm_subscription", args=[subscription_token(sub, "confirm")])
            subject = f"确认订阅 · {settings.SITE_TITLE}"
            text = f"你为一条提问留下了通知邮箱。\n\n打开链接并确认，之后才会收到回答和更新通知：\n{url}\n\n链接两天内有效。如果不是你操作的，请忽略；不会自动订阅。"
        else:
            url = settings.SITE_URL + reverse("question", args=[question.pk])
            subject = f"{'你的问题收到回答了' if job.event == 'answered' else '你的回答有更新'} · {settings.SITE_TITLE}"
            text = f"{settings.OWNER_NAME} {'已回答你的问题' if job.event == 'answered' else '更新了答案'}。\n\n查看最新回答：\n{url}\n\n停止这条问题的通知：\n{cancel_url}\n\n为保护隐私，邮件不包含提问原文。"
        if send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [sub.email], fail_silently=False) != 1:
            raise RuntimeError("MailNotAccepted")
        MailJob.objects.filter(pk=job_id).update(status="sent", lease_until=None, last_error="")
    except MailJob.DoesNotExist:
        pass  # The owner deleted the question while the job was leased.
    except Exception as exc:
        MailJob.objects.filter(pk=job_id).update(
            status="failed" if job.attempts >= 5 else "pending", lease_until=None,
            available_at=timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** job.attempts)),
            last_error=type(exc).__name__[:80],
        )
    return True
