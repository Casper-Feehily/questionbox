"""JSON API for the reader-only WeChat mini program. Browser sessions grant no API access."""
import json
import re
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.paginator import Paginator, EmptyPage
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .forms import AskForm
from .browsing import published, public_selection
from .graph import notifications_enabled
from .models import Question, WechatSession, WechatReminder, WechatMessageJob
from .rate_limit import RateLimited, consume
from .submission import SubmissionError, submit_question
from . import wechat


def error(code, message, status=400, **extra):
    return JsonResponse({"error": {"code": code, "message": message, **extra}}, status=status)


def endpoint(methods, private=False):
    def decorate(view):
        @csrf_exempt  # Only these endpoints use bearer auth; browser views retain CSRF.
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = error("method_not_allowed", "不支持此操作。", 405)
                response["Allow"] = ", ".join(methods)
                return response
            if private:
                if not settings.WECHAT_ENABLED:
                    return error("wechat_unavailable", "微信提问功能暂未启用。", 503)
                auth = request.headers.get("Authorization", "")
                if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", auth):
                    return error("login_required", "请重新登录后继续。", 401)
                session = WechatSession.objects.select_related("identity").filter(
                    token_digest=wechat.token_digest(auth[7:]), expires_at__gt=timezone.now(),
                    identity__appid=settings.WECHAT_APPID).first()
                if not session:
                    return error("login_required", "登录已过期，请重新登录。", 401)
                request.wechat_identity = session.identity
            if request.method == "POST":
                try:
                    if len(request.body) > 16 * 1024:
                        return error("request_too_large", "请求过大，请只发送文字。", 413)
                    payload = json.loads(request.body)
                except RequestDataTooBig:
                    return error("request_too_large", "请求过大，请只发送文字。", 413)
                except (ValueError, UnicodeDecodeError):
                    return error("invalid_json", "请求格式有误，请重试。")
                if not isinstance(payload, dict):
                    return error("invalid_json", "请求必须是 JSON 对象。")
                request.payload = payload
            try:
                return view(request, *args, **kwargs)
            except Http404:
                return error("not_found", "内容已不存在。", 404)
            except RateLimited as exc:
                response = error("rate_limited", f"请等待 {exc.seconds} 秒再试。", 429, retry_after=exc.seconds)
                response["Retry-After"] = str(exc.seconds)
                return response
            except SubmissionError as exc:
                return error(exc.code, exc.message, 409)
        return wrapped
    return decorate


def serialize(question, private=False):
    published = bool(question.answered_at and question.answer)
    data = {"id": str(question.pk), "text": question.text, "signature": question.signature,
            "display_name": question.display_name, "created_at": question.created_at.isoformat(),
            "answer": question.answer if published else None,
            "answered_at": question.answered_at.isoformat() if published else None,
            "answer_updated_at": question.answer_updated_at.isoformat() if published and question.answer_updated_at else None,
            "version": question.version if published else 0, "status": "answered" if published else "pending",
            "is_featured": question.is_featured if published else False,
            "tags": [{"id": tag.pk, "name": tag.name} for tag in question.tags.all()] if published else []}
    if private:
        reminder = WechatReminder.objects.filter(question=question).first()
        job = WechatMessageJob.objects.filter(reminder=reminder).first() if reminder else None
        data["wechat_reminder"] = {"enabled": bool(reminder and reminder.active), "status": job.status if job else "none"}
    return data


def published_questions():
    return published().prefetch_related("tags").order_by("-answer_updated_at", "-id")


def listing(request, queryset, private=False, extra=None):
    try:
        page_number = int(request.GET.get("page", "1"))
        if page_number < 1:
            raise ValueError
    except ValueError:
        return error("invalid_page", "页码有误。")
    size = 10 if private else {"7": 7, "14": 14}.get(request.GET.get("per_page"), 5)
    paginator = Paginator(queryset, size)
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        return JsonResponse({"items": [], "page": page_number, "has_more": False, "count": paginator.count, **(extra or {})})
    return JsonResponse({"items": [serialize(q, private) for q in page], "page": page.number,
                         "has_more": page.has_next(), "count": paginator.count, **(extra or {})})


@endpoint(["GET"])
def meta(request):
    return JsonResponse({"site_title": settings.SITE_TITLE, "owner_name": settings.OWNER_NAME,
                         "max_characters": 700, "max_signature": 30, "capacity": settings.MAX_QUESTIONS,
                         "box_full": Question.objects.count() >= settings.MAX_QUESTIONS,
                         "wechat_enabled": settings.WECHAT_ENABLED, "email_enabled": notifications_enabled(),
                         "reminders_enabled": wechat.reminders_enabled(),
                         "reply_template_id": settings.WECHAT_REPLY_TEMPLATE_ID if wechat.reminders_enabled() else None})


@endpoint(["POST"])
def session(request):
    if not settings.WECHAT_ENABLED:
        return error("wechat_unavailable", "微信提问功能暂未启用。", 503)
    code = request.payload.get("code")
    if set(request.payload) != {"code"} or not isinstance(code, str) or not 1 <= len(code) <= 256:
        return error("invalid_code", "微信登录凭证有误。")
    consume([("wechat-login-ip", request.client_address, 30, 900),
             ("wechat-login-site", "site", settings.RATE_SITE_HOUR, 3600)])
    try:
        openid = wechat.exchange_code(code)
    except wechat.WechatError as exc:
        if exc.code in {40029, 40163}:
            return error("invalid_code", "微信登录凭证已失效，请重试。", 401)
        return error("wechat_unavailable", "微信登录暂时不可用，请稍后重试。", 503)
    except Exception:
        return error("wechat_unavailable", "微信登录暂时不可用，请稍后重试。", 503)
    token, expires = wechat.create_session(openid)
    return JsonResponse({"token": token, "expires_at": expires.isoformat()})


@endpoint(["GET", "POST"])
def questions(request):
    if request.method == "GET":
        scope = public_selection(request.GET, default_view="all")
        queryset = scope.pop("questions")
        scope["tags"] = [{"id": tag.pk, "name": tag.name} for tag in scope["tags"]]
        tag = scope["selected_tag"]
        scope["selected_tag"] = {"id": tag.pk, "name": tag.name} if tag else None
        return listing(request, queryset, extra=scope)
    return create_question(request)


@endpoint(["POST"], private=True)
@transaction.atomic
def create_question(request):
    payload = request.payload
    allowed = {"text", "signature", "email", "client_request_id"}
    if set(payload) - allowed or any(not isinstance(v, str) for v in payload.values()):
        return error("invalid_fields", "只接受文字问题、署名和可选邮箱。")
    try:
        request_id = UUID(payload.get("client_request_id", ""))
    except (ValueError, TypeError, AttributeError):
        return error("invalid_request_id", "缺少有效的投递标识，请重试。")
    # Validate a retry even if email service was disabled after the original request.
    form = AskForm({key: payload.get(key, "") for key in ("text", "signature", "email")})
    if not form.is_valid():
        # A committed replay is still successful when notification configuration changes.
        existing = Question.objects.filter(wechat_identity=request.wechat_identity, client_request_id=request_id).exists()
        if not existing or set(form.errors) != {"email"} or notifications_enabled():
            return error("invalid_fields", "请检查填写的内容。", fields={k: list(v) for k, v in form.errors.items()})
        from django import forms
        try:
            email = forms.EmailField(required=False).clean(payload.get("email", "")).lower()
        except ValidationError:
            return error("invalid_fields", "请输入有效邮箱。")
        cleaned = {**form.cleaned_data, "email": email}
    else:
        cleaned = form.cleaned_data
    question, created = submit_question(request, cleaned, request.wechat_identity, request_id)
    return JsonResponse({"question": serialize(question, True), "created": created}, status=201 if created else 200)


@endpoint(["GET"])
def question_detail(request, pk):
    return JsonResponse({"question": serialize(get_object_or_404(published_questions(), pk=pk))})


@endpoint(["GET"], private=True)
def my_questions(request):
    return listing(request, Question.objects.filter(wechat_identity=request.wechat_identity).prefetch_related("tags").order_by("-created_at", "-id"), True)


@endpoint(["GET"], private=True)
def my_question_detail(request, pk):
    return JsonResponse({"question": serialize(get_object_or_404(Question, pk=pk, wechat_identity=request.wechat_identity), True)})


@endpoint(["POST"], private=True)
def reminder(request, pk):
    if set(request.payload) != {"enabled"} or type(request.payload["enabled"]) is not bool:
        return error("invalid_fields", "请指定是否接收提醒。")
    enabled = request.payload["enabled"]
    if enabled and not wechat.reminders_enabled():
        return error("reminder_unavailable", "微信回复提醒暂未启用。", 503)
    with transaction.atomic():
        question = get_object_or_404(Question.objects.select_for_update(), pk=pk, wechat_identity=request.wechat_identity)
        if enabled:
            sub, _ = WechatReminder.objects.update_or_create(question=question, defaults={"active": True, "subscribed_at": timezone.now()})
            wechat.queue_reply(question)
        else:
            WechatReminder.objects.filter(question=question).update(active=False)
            WechatMessageJob.objects.filter(reminder__question=question, status__in=["pending", "failed", "sending"]).update(
                status="cancelled", lease_until=None)
    return JsonResponse({"question": serialize(question, True)})
