from functools import wraps
import secrets
import time
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.core import signing
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from .forms import AnswerForm, AskForm
from .mail import get_subscription, queue_answer
from .models import MailJob, Question, ServerChanJob, Subscription
from .rate_limit import RateLimited, consume
from .serverchan import queue_question
from . import graph
from .submission import SubmissionError, submit_question


def owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.user.is_active or not request.user.is_superuser:
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def home_context(request, form=None):
    questions = Question.objects.filter(answered_at__isnull=False).exclude(answer="").order_by("-answer_updated_at", "-id")
    per_page = 7 if request.GET.get("per_page") == "7" else 14
    paginator = Paginator(questions, per_page)
    return {"form": form or AskForm(), "page": paginator.get_page(request.GET.get("page")),
            "per_page": per_page, "answered_count": paginator.count,
            "box_full": Question.objects.count() >= settings.MAX_QUESTIONS}


@require_GET
def home(request):
    return render(request, "home.html", home_context(request))


@require_POST
def ask(request):
    form = AskForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "home.html", home_context(request, form), status=400)
    try:
        submit_question(request, form.cleaned_data)
    except SubmissionError as exc:
        form.add_error(None, exc.message)
        return render(request, "home.html", home_context(request, form), status=409)
    except RateLimited as exc:
        form.add_error(None, f"提出好问题需要一点时间 ☕ 让好奇心酝酿一下，还需等待 {exc.seconds} 秒再投递。")
        response = render(request, "home.html", home_context(request, form), status=429)
        response["Retry-After"] = str(exc.seconds)
        return response
    return redirect("thanks")


@require_GET
def thanks(request):
    return render(request, "message.html", {"headline": "收到你的好奇心了！", "message": "问题已进入私人收件箱，回答并发布之后才会出现在这里。如果填写了邮箱，已自动订阅这条问题的回信，并会收到订阅成功邮件。"})


@require_GET
def question_detail(request, pk):
    question = get_object_or_404(Question.objects.filter(answered_at__isnull=False).exclude(answer=""), pk=pk)
    return render(request, "question.html", {"question": question})


@require_GET
def privacy(request):
    return render(request, "privacy.html")


def resolve_subscription(token, purpose):
    try:
        return get_subscription(token, purpose)
    except (signing.BadSignature, Subscription.DoesNotExist, ValueError, TypeError):
        raise Http404 from None


@require_http_methods(["GET", "POST"])
def confirm_subscription(request, token):
    sub = resolve_subscription(token, "confirm")
    if request.method == "POST":
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().select_related("question").get(pk=sub.pk)
            if sub.active and not sub.subscribed_at:
                sub.subscribed_at = timezone.now()
                sub.save(update_fields=["subscribed_at"])
                if sub.question.answered_at:
                    queue_answer(sub.question, "answered")
        return render(request, "message.html", {"headline": "订阅已确认" if sub.active else "订阅已停止", "message": "这条问题收到回答或发布更新时，我们会通知你。" if sub.active else "这条问题不会再发送邮件。"})
    return render(request, "subscription.html", {"headline": "确认接收这条问题的回信", "button": "确认订阅", "description": "确认之后，才会收到首次回答与后续更新的邮件。"})


@require_http_methods(["GET", "POST"])
def unsubscribe(request, token):
    sub = resolve_subscription(token, "unsubscribe")
    if request.method == "POST":
        with transaction.atomic():
            Subscription.objects.filter(pk=sub.pk).update(active=False)
            MailJob.objects.filter(subscription=sub, status__in=["pending", "sending"]).update(status="cancelled")
        return render(request, "message.html", {"headline": "已停止通知", "message": "这条问题不会再给你发送邮件。你仍然可以在网站查看公开回答。"})
    return render(request, "subscription.html", {"headline": "停止这条问题的通知？", "button": "停止通知", "description": "只停止这条问题的邮件，不影响查看公开回答。"})


@require_http_methods(["GET", "POST"])
def owner_login(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect("owner")
    form = AuthenticationForm(request, data=request.POST if request.method == "POST" else None)
    if request.method == "POST":
        try:
            consume([("login-ip", request.client_address, 5, 900)])
        except RateLimited as exc:
            response = render(request, "login.html", {"form": form, "throttled": True}, status=429)
            response["Retry-After"] = str(exc.seconds)
            return response
        if form.is_valid():
            user = form.get_user()
            if user.is_superuser:
                login(request, user)
                # Always use a fixed local destination; never follow an unchecked next URL.
                return redirect("owner")
            form.add_error(None, "此账号没有主人权限。")
    return render(request, "login.html", {"form": form})


@require_POST
def owner_logout(request):
    logout(request)
    return redirect("home")


@owner_required
@require_GET
def owner_inbox(request):
    selected = request.GET.get("state", "pending")
    questions = Question.objects.order_by("-created_at")
    if selected == "answered":
        questions = questions.filter(answered_at__isnull=False)
    else:
        selected = "pending"
        questions = questions.filter(answered_at__isnull=True)
    return render(request, "owner.html", {"page": Paginator(questions, 20).get_page(request.GET.get("page")),
        "selected": selected, "pending_count": Question.objects.filter(answered_at__isnull=True).count(),
        "published_count": Question.objects.filter(answered_at__isnull=False).count(),
        "failed_mail_count": MailJob.objects.filter(status="failed").count(),
        "failed_notification_count": ServerChanJob.objects.filter(status="failed").count(),
        "stored_count": Question.objects.count(), "capacity": settings.MAX_QUESTIONS})


@owner_required
@require_http_methods(["GET", "POST"])
def owner_question(request, pk):
    question = get_object_or_404(Question, pk=pk)
    form = AnswerForm(request.POST if request.method == "POST" else None,
                      initial={"answer": question.draft or question.answer, "expected_version": question.version})
    status = 200
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            question = get_object_or_404(Question.objects.select_for_update(), pk=pk)
            if form.cleaned_data["expected_version"] != question.version:
                form.add_error(None, "答案已在其他页面更新。请重新打开此问题后再编辑，避免覆盖新的回答。")
                status = 409
            elif request.POST.get("action") == "draft":
                question.draft = form.cleaned_data["answer"]
                question.save(update_fields=["draft"])
                messages.success(request, "草稿已保存，公开回答保持原样。")
                return redirect("owner_question", pk=question.pk)
            elif request.POST.get("action") == "publish":
                answer = form.cleaned_data["answer"]
                changed = answer != question.answer
                event = "answered" if question.answered_at is None else "updated"
                if changed:
                    now = timezone.now()
                    question.answer, question.draft = answer, ""
                    question.answered_at = question.answered_at or now
                    question.answer_updated_at = now
                    question.version += 1
                    question.save()
                    queue_answer(question, event)
                elif question.draft:
                    question.draft = ""
                    question.save(update_fields=["draft"])
                messages.success(request, "回答已发布。" if changed else "内容没有变化，无需重复发布。")
                return redirect("owner_question", pk=question.pk)
            else:
                form.add_error(None, "请选择保存草稿或发布回答。")
                status = 400
    elif request.method == "POST":
        status = 400
    return render(request, "owner_question.html", {"question": question, "form": form}, status=status)


@owner_required
@require_POST
def owner_delete(request, pk):
    with transaction.atomic():
        get_object_or_404(Question.objects.select_for_update(), pk=pk).delete()
    messages.success(request, "问题及其答案、订阅和待发邮件已删除。")
    return redirect("owner")


class TestMailForm(forms.Form):
    email = forms.EmailField(label="测试收件邮箱", max_length=254,
                             widget=forms.EmailInput(attrs={"autocomplete": "email"}))


@owner_required
@require_GET
def owner_mail(request):
    status = graph.connection_status()
    return render(request, "owner_mail.html", {
        "mail_status": status, "graph_selected": settings.EMAIL_BACKEND == graph.BACKEND,
        "notifications_enabled": graph.notifications_enabled(), "mail_requested": settings.EMAIL_ENABLED,
        "client_id": settings.MS_GRAPH_CLIENT_ID, "tenant_id": settings.MS_GRAPH_TENANT_ID,
        "account_type": settings.MS_GRAPH_ACCOUNT_TYPE, "redirect_uri": graph.redirect_uri(),
        "test_form": TestMailForm(initial={"email": status["sender"]}),
    })


@owner_required
@require_POST
def mail_connect(request):
    request.session.pop(graph.FLOW_SESSION_KEY, None)
    try:
        flow = graph.begin_authorization()
        request.session[graph.FLOW_SESSION_KEY] = {"flow": flow, "created_at": time.time(),
                                                  "client_id": settings.MS_GRAPH_CLIENT_ID,
                                                  "authority": graph.authority()}
        response = redirect(flow["auth_uri"])
    except graph.GraphError as exc:
        messages.error(request, str(exc))
        response = redirect("owner_mail")
    response["Referrer-Policy"] = "no-referrer"
    return response


@owner_required
@require_GET
def mail_callback(request):
    saved = request.session.pop(graph.FLOW_SESSION_KEY, None)
    try:
        if not saved or any(len(values) != 1 for _, values in request.GET.lists()):
            raise graph.GraphError("授权已过期或信息不完整，请重新连接邮箱。")
        elapsed = time.time() - saved["created_at"]
        if not 0 <= elapsed <= 600 or saved["client_id"] != settings.MS_GRAPH_CLIENT_ID or saved["authority"] != graph.authority():
            raise graph.GraphError("授权已过期或配置已变更，请重新连接邮箱。")
        if not secrets.compare_digest(saved["flow"].get("state", ""), request.GET.get("state", "")):
            raise graph.GraphError("授权信息不匹配，请重新连接邮箱。")
        sender = graph.complete_authorization(saved["flow"], request.GET.dict())
        messages.success(request, f"Microsoft 邮箱已连接：{sender}" if sender else "Microsoft 邮箱已连接。")
    except graph.GraphError as exc:
        messages.error(request, str(exc))
    except OSError:
        messages.error(request, "邮箱授权无法保存，请检查服务器数据目录后重试。")
    response = redirect("owner_mail")
    response["Referrer-Policy"] = "no-referrer"
    return response


@owner_required
@require_POST
def mail_disconnect(request):
    request.session.pop(graph.FLOW_SESSION_KEY, None)
    graph.disconnect()
    messages.success(request, "邮箱已断开，访客邮件通知已关闭。")
    return redirect("owner_mail")


@owner_required
@require_POST
def mail_test(request):
    form = TestMailForm(request.POST)
    if not graph.notifications_enabled():
        messages.error(request, "请先连接邮箱并启用邮件通知。")
    elif not form.is_valid():
        messages.error(request, "请填写有效的测试收件邮箱。")
    else:
        try:
            consume([("mail-test-owner", str(request.user.pk), 1, 60)])
            if send_mail(f"邮件通知测试 · {settings.SITE_TITLE}",
                         "邮件通知已连接。访客填写邮箱后会自动订阅，并收到回答和更新通知。",
                         settings.DEFAULT_FROM_EMAIL, [form.cleaned_data["email"]], fail_silently=False) != 1:
                raise graph.GraphDeliveryError("邮件未被接受。")
            messages.success(request, "测试邮件已提交发送，请检查收件箱和垃圾邮件。")
        except RateLimited as exc:
            messages.error(request, f"请等待 {exc.seconds} 秒后再测试。")
        except Exception:
            messages.error(request, "测试邮件发送失败，请检查授权、客户端密码和 Microsoft 邮箱服务。")
    return redirect("owner_mail")
