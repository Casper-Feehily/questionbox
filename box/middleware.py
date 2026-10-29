import ipaddress
import re
import uuid
from django.conf import settings
from django.core import signing
from django.http import HttpResponse, JsonResponse


def client_address(request):
    try:
        peer = ipaddress.ip_address(request.META.get("REMOTE_ADDR", "127.0.0.1"))
    except ValueError:
        return "invalid-address"
    # Walk from the trusted edge toward the client; never trust arbitrary leftmost input.
    if any(peer in network for network in settings.TRUSTED_PROXY_CIDRS):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        try:
            addresses = [ipaddress.ip_address(value.strip()) for value in forwarded.split(",") if value.strip()]
        except ValueError:
            addresses = []
        for address in reversed(addresses):
            if not any(peer in network for network in settings.TRUSTED_PROXY_CIDRS):
                break
            peer = address
    if isinstance(peer, ipaddress.IPv6Address):
        if peer.ipv4_mapped:
            return str(peer.ipv4_mapped)
        # Limit temporary IPv6 address rotation within one subscriber's /64.
        return str(ipaddress.ip_network(f"{peer}/64", strict=False))
    return str(peer)


class BoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_api = request.path.startswith("/api/v1/")
        def rejected(code, message, status):
            response = JsonResponse({"error": {"code": code, "message": message}}, status=status) if is_api else HttpResponse(message, status=status)
            response["Cache-Control"] = "no-store"
            return response
        peer = request.META.get("REMOTE_ADDR", "")
        try:
            trusted = any(ipaddress.ip_address(peer) in net for net in settings.TRUSTED_PROXY_CIDRS)
        except ValueError:
            trusted = False
        # SecurityMiddleware reads this in request.is_secure(). Only a trusted proxy may assert TLS.
        if trusted and request.META.get("HTTP_X_FORWARDED_PROTO") == "https":
            request.META["wsgi.url_scheme"] = "https"
        if request.path.startswith(settings.STATIC_URL):
            return self.get_response(request)
        request.client_address = client_address(request)
        try:
            visitor = request.get_signed_cookie(settings.VISITOR_COOKIE, salt=settings.VISITOR_COOKIE_SALT,
                                                max_age=settings.VISITOR_COOKIE_AGE)
            if not re.fullmatch(r"[a-f0-9]{32}", visitor):
                raise signing.BadSignature()
            fresh = False
        except (KeyError, signing.BadSignature):
            visitor, fresh = uuid.uuid4().hex, True
        request.visitor_id = visitor
        if request.method == "POST":
            content_type = request.META.get("CONTENT_TYPE", "").split(";", 1)[0]
            try:
                limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE if request.path.startswith("/owner/") else 16 * 1024
                too_large = int(request.META.get("CONTENT_LENGTH") or 0) > limit
            except ValueError:
                too_large = True
            if too_large:
                return rejected("request_too_large", "请求过大。请只发送文字。", 413)
            expected = "application/json" if is_api else "application/x-www-form-urlencoded"
            if content_type != expected:
                return rejected("unsupported_media_type", "只接受 JSON 文字请求。" if is_api else "只接受文字表单，不接收文件或图片。", 415)
        response = self.get_response(request)
        if is_api and response.status_code >= 400 and not response.get("Content-Type", "").startswith("application/json"):
            response = rejected("not_found" if response.status_code == 404 else "request_failed",
                                "内容已不存在。" if response.status_code == 404 else "请求未完成，请稍后重试。", response.status_code)
        if fresh and not is_api:
            response.set_signed_cookie(settings.VISITOR_COOKIE, visitor, salt=settings.VISITOR_COOKIE_SALT,
                                       max_age=settings.VISITOR_COOKIE_AGE, httponly=True,
                                       secure=not settings.DEBUG, samesite="Lax")
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        response["Cache-Control"] = "no-store"
        if request.path.startswith(("/owner/", "/subscription/", "/thanks/", "/api/")):
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
