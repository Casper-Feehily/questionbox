"""Transactional submission of browser forms."""
from django.conf import settings
from django.db import transaction
from .models import MailJob, Question, Subscription
from .rate_limit import consume
from .serverchan import queue_question
from .bark import queue_question as queue_bark_question


class SubmissionError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def question_limits(request):
    if not settings.QUESTION_RATE_LIMIT_ENABLED:
        return []
    visitor = request.visitor_id
    return [
        ("ask-ip", request.client_address, settings.RATE_IP_HOUR, 3600),
        ("ask-cookie-minute", visitor, settings.RATE_COOKIE_MINUTE, 60),
        ("ask-cookie-hour", visitor, settings.RATE_COOKIE_HOUR, 3600),
        ("ask-site", "site", settings.RATE_SITE_HOUR, 3600),
    ]


@transaction.atomic
def submit_question(request, cleaned):
    # SQLite BEGIN IMMEDIATE serializes capacity checking and insertion.
    if Question.objects.count() >= settings.MAX_QUESTIONS:
        raise SubmissionError("box_full", f"提问箱已满，请联系 {settings.OWNER_NAME} 解决后再投递。")
    limits = question_limits(request)
    email = cleaned.get("email", "")
    if email:
        limits.extend([("email-short", email, 1, 600), ("email-day", email, 6, 86400)])
    consume(limits)
    if cleaned.get("website"):
        return None, False
    question = Question.objects.create(text=cleaned["text"], signature=cleaned.get("signature", ""))
    queue_question(question)
    queue_bark_question(question)
    if email:
        sub = Subscription.objects.create(question=question, email=email)
        MailJob.objects.create(subscription=sub, event="subscribed")
    return question, True
