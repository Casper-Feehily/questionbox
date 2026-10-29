"""Shared public visibility and single-tag filtering for the wall and reader."""
from .models import Question, Tag


def published():
    return Question.objects.filter(answered_at__isnull=False).exclude(answer="")


def tag_choices(questions):
    return Tag.objects.filter(questions__in=questions).distinct()


def filter_tag(questions, tags, raw):
    if not raw:
        return questions, None, False
    try:
        tag_id = int(raw)
        tag = tags.filter(pk=tag_id).first() if 0 < tag_id < 2 ** 63 else None
    except (ValueError, TypeError):
        tag = None
    return (questions.filter(tags=tag) if tag else questions.none()), tag, True


def public_selection(params, default_view="featured"):
    """One public scope for the website and mini program; only published tags escape."""
    public = published()
    default = "all" if params.get("tag") else default_view
    selected_view = params.get("view", default)
    if selected_view == "categories":
        selected_view = "featured"
    if selected_view not in {"featured", "all"}:
        selected_view = default
    tags = tag_choices(public)
    questions, selected_tag, has_tag_filter = public.filter(is_featured=True), None, False
    if selected_view == "all":
        questions, selected_tag, has_tag_filter = filter_tag(public, tags, params.get("tag"))
    return {"questions": questions.prefetch_related("tags").order_by("-answer_updated_at", "-id"),
            "selected_view": selected_view, "tags": tags, "selected_tag": selected_tag,
            "tag_param": selected_tag.pk if selected_tag else ("invalid" if has_tag_filter else ""),
            "has_tag_filter": has_tag_filter, "public_answer_count": public.count(),
            "public_topic_count": tags.count()}
