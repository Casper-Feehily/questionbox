"""One transactional submission path for browser forms and the mini program."""
import json
from django.conf import settings
from django.db import transaction
from django.utils.crypto import salted_hmac
from .models import MailJob, Question, Subscription
from .rate_limit import consume
from .serverchan import queue_question
from .bark import queue_question as queue_bark_question


class SubmissionError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def question_limits(request, identity=None):
    if not settings.QUESTION_RATE_LIMIT_ENABLED:
        return []
    scope = "wechat" if identity else "cookie"
    visitor = str(identity.pk) if identity else request.visitor_id
    return [
        ("ask-ip", request.client_address, settings.RATE_IP_HOUR, 3600),
        (f"ask-{scope}-minute", visitor, settings.RATE_COOKIE_MINUTE, 60),
        (f"ask-{scope}-hour", visitor, settings.RATE_COOKIE_HOUR, 3600),
        ("ask-site", "site", settings.RATE_SITE_HOUR, 3600),
    ]


@transaction.atomic
def submit_question(request, cleaned, identity=None, request_id=None):
    # Stable HMAC includes the email without persisting another plaintext copy.
    digest = salted_hmac("questionbox.submission.v1", json.dumps(
        {key: cleaned.get(key, "") for key in ("text", "signature", "email")},
        sort_keys=True, ensure_ascii=False), algorithm="sha256").hexdigest() if identity else ""
    if identity:
        existing = Question.objects.filter(wechat_identity=identity, client_request_id=request_id).first()
        if existing:
            if existing.submission_digest != digest:
                raise SubmissionError("submission_conflict", "这次投递标识已用于其他内容，请重新投递。")
            return existing, False
    # SQLite BEGIN IMMEDIATE serializes both deduplication and capacity checking.
    if Question.objects.count() >= settings.MAX_QUESTIONS:
        raise SubmissionError("box_full", f"提问箱已满，请联系 {settings.OWNER_NAME} 解决后再投递。")
    limits = question_limits(request, identity)
    email = cleaned.get("email", "")
    if email:
        limits.extend([("email-short", email, 1, 600), ("email-day", email, 6, 86400)])
    consume(limits)
    if cleaned.get("website"):
        return None, False
    question = Question.objects.create(text=cleaned["text"], signature=cleaned.get("signature", ""),
                                       wechat_identity=identity, client_request_id=request_id,
                                       submission_digest=digest)
    queue_question(question)
    queue_bark_question(question)
    if email:
        sub = Subscription.objects.create(question=question, email=email)
        MailJob.objects.create(subscription=sub, event="subscribed")
    return question, True
