"""Delegated Microsoft Graph mail, with a shared encrypted MSAL cache."""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
import msal
import requests

BACKEND = "box.graph_backend.EmailBackend"
SCOPES = ["https://graph.microsoft.com/Mail.Send"]
FLOW_SESSION_KEY = "questionbox.graph.flow"


class GraphError(Exception):
    pass


class GraphReauthorizationRequired(GraphError):
    pass


class GraphDeliveryError(GraphError):
    pass


def configured():
    return bool(settings.MS_GRAPH_CLIENT_ID and settings.MS_GRAPH_CLIENT_SECRET and
                (settings.MS_GRAPH_ACCOUNT_TYPE == "personal" or settings.MS_GRAPH_TENANT_ID))


def authority():
    tenant = "consumers" if settings.MS_GRAPH_ACCOUNT_TYPE == "personal" else settings.MS_GRAPH_TENANT_ID
    return f"https://login.microsoftonline.com/{tenant}"


def redirect_uri():
    return settings.SITE_URL + reverse("mail_callback")


def _cipher():
    key = hashlib.sha256(b"questionbox.graph.cache.v1\0" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _read_state():
    try:
        state = json.loads(_cipher().decrypt((settings.DATA_DIR / "outlook-tokens.enc").read_bytes()))
        if not isinstance(state, dict):
            raise ValueError
        return state
    except FileNotFoundError:
        return {}
    except (InvalidToken, ValueError, TypeError):
        return {"requires_reauth": True}


@contextmanager
def _store():
    """Serialize web/worker refreshes and atomically persist token changes."""
    path = settings.DATA_DIR / "outlook-tokens.enc"
    fd = os.open(settings.DATA_DIR / ".outlook-tokens.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = _read_state()
            original = json.dumps(state, sort_keys=True)
            try:
                yield state
            finally:
                serialized = json.dumps(state, sort_keys=True)
                if serialized != original:
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=settings.DATA_DIR, prefix=".outlook-", delete=False) as out:
                            temp_path = out.name
                            out.write(_cipher().encrypt(serialized.encode()))
                            out.flush()
                            os.fsync(out.fileno())
                        os.replace(temp_path, path)
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            os.unlink(temp_path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _matches(state):
    return state.get("client_id") == settings.MS_GRAPH_CLIENT_ID and state.get("authority") == authority()


def connection_status():
    result = {"configured": configured(), "connected": False, "sender": "", "requires_reauth": False}
    if not settings.MS_GRAPH_CLIENT_ID:
        return result
    try:
        # Atomic replacement permits reads without waiting on a worker's network refresh.
        state = _read_state()
        result["requires_reauth"] = bool(state.get("requires_reauth"))
        if _matches(state):
            result["sender"] = state.get("sender", "")
            result["connected"] = bool(result["configured"] and state.get("account_id") and
                                        state.get("cache") and not result["requires_reauth"])
    except OSError:
        result["requires_reauth"] = True
    return result


def notifications_enabled():
    if not settings.EMAIL_ENABLED:
        return False
    if settings.EMAIL_BACKEND == BACKEND:
        return connection_status()["connected"]
    return True


def _application(cache=None):
    if not configured():
        raise GraphError("请先在服务器配置 Microsoft 应用的客户端密码。")
    try:
        return msal.ConfidentialClientApplication(
            settings.MS_GRAPH_CLIENT_ID, client_credential=settings.MS_GRAPH_CLIENT_SECRET,
            authority=authority(), token_cache=cache, timeout=settings.EMAIL_TIMEOUT,
            enable_pii_log=False,
        )
    except (requests.RequestException, ValueError):
        raise GraphError("暂时无法连接 Microsoft，请检查应用配置后重试。") from None


def begin_authorization():
    try:
        # MSAL generates state, nonce and PKCE, and adds offline_access itself.
        flow = _application().initiate_auth_code_flow(
            SCOPES, redirect_uri=redirect_uri(), prompt="select_account", response_mode="query")
    except requests.RequestException:
        raise GraphError("暂时无法连接 Microsoft，请稍后重试。") from None
    if "auth_uri" not in flow:
        raise GraphError("无法开始授权，请检查 Microsoft 应用配置。")
    return flow


def complete_authorization(flow, response):
    # A fresh cache keeps a failed/rejected login from replacing the working mailbox.
    cache = msal.SerializableTokenCache()
    app = _application(cache)
    try:
        result = app.acquire_token_by_auth_code_flow(flow, response)
    except ValueError:
        raise GraphError("授权信息不匹配，请重新连接邮箱。") from None
    except requests.RequestException:
        raise GraphError("暂时无法连接 Microsoft，请重新尝试授权。") from None
    if not result.get("access_token"):
        codes = result.get("error_codes", [])
        if 7000215 in codes or 7000222 in codes:
            raise GraphError("客户端密码无效或已过期，请更新服务器配置。")
        raise GraphError("Microsoft 授权未完成，请检查账户类型及 Mail.Send 委托权限后重试。")
    scopes = {scope.rsplit("/", 1)[-1].lower() for scope in result.get("scope", "").split()}
    accounts = app.get_accounts()
    if "mail.send" not in scopes or len(accounts) != 1:
        raise GraphError("没有获得 Mail.Send 授权，请检查委托权限后重新连接。")
    account = accounts[0]
    sender = account.get("username", "")
    with _store() as state:
        state.clear()
        state.update(client_id=settings.MS_GRAPH_CLIENT_ID, authority=authority(),
                     account_id=account["home_account_id"], sender=sender,
                     connected_at=timezone.now().isoformat(), cache=cache.serialize(), requires_reauth=False)
    return sender


def disconnect():
    with _store() as state:
        state.clear()


def access_token(force_refresh=False):
    with _store() as state:
        if not _matches(state) or not state.get("account_id") or state.get("requires_reauth"):
            raise GraphReauthorizationRequired("请在主人后台连接或重新授权 Microsoft 邮箱。")
        cache = msal.SerializableTokenCache()
        try:
            cache.deserialize(state.get("cache", "{}"))
        except (ValueError, TypeError):
            state["requires_reauth"] = True
            raise GraphReauthorizationRequired("请重新授权 Microsoft 邮箱。") from None
        app = _application(cache)
        account = next((a for a in app.get_accounts() if a["home_account_id"] == state["account_id"]), None)
        try:
            result = app.acquire_token_silent_with_error(SCOPES, account=account, force_refresh=force_refresh)
        except requests.RequestException:
            raise GraphError("暂时无法更新 Microsoft 授权，请稍后重试。") from None
        finally:
            if cache.has_state_changed:
                state["cache"] = cache.serialize()
        if result and result.get("access_token"):
            return result["access_token"]
        if not result or result.get("error") in {"invalid_grant", "interaction_required", "consent_required", "login_required"}:
            state["requires_reauth"] = True
            raise GraphReauthorizationRequired("Microsoft 授权已失效，请在主人后台重新连接邮箱。")
        raise GraphError("Microsoft 授权更新失败，请检查客户端密码或稍后重试。")
