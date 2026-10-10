"""Public, shuffled reading; load only a few complete letters at a time."""
import secrets
from uuid import UUID

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import Question


def published():
    return Question.objects.filter(answered_at__isnull=False).exclude(answer="")


def serialize(question):
    updated = question.answer_updated_at or question.answered_at
    return {
        "id": str(question.pk), "text": question.text,
        "display_name": question.display_name, "answer": question.answer,
        "date": timezone.localtime(updated).strftime("%Y.%m.%d"),
        "updated": question.version > 1,
        "url": reverse("question", args=[question.pk]),
    }


@require_GET
def reader(request):
    order = list(published().values_list("pk", flat=True))
    secrets.SystemRandom().shuffle(order)
    initial = {question.pk: serialize(question) for question in published().filter(pk__in=order[:3])}
    cards = [initial[pk] for pk in order[:3] if pk in initial]
    return render(request, "reader.html", {
        "cards": cards,
        "reader_data": {"order": [str(pk) for pk in order], "cards": cards},
        "total": len(order),
    })


@require_GET
def cards(request):
    # The client sends its next few public IDs, never a private session or draft.
    raw = request.GET.get("ids", "").split(",")
    if not 1 <= len(raw) <= 6:
        return JsonResponse({"error": "每次最多读取六封回信。"}, status=400)
    try:
        ids = list(dict.fromkeys(UUID(value) for value in raw))
    except (ValueError, AttributeError):
        return JsonResponse({"error": "回信编号有误。"}, status=400)
    found = {question.pk: serialize(question) for question in published().filter(pk__in=ids)}
    return JsonResponse({"cards": [found[pk] for pk in ids if pk in found]})
