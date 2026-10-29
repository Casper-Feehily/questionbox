import ipaddress
import json
import re
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
# Read literal KEY=value pairs; never execute configuration as shell code.
env_file = BASE_DIR / ".env"
if env_file.is_file():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def flag(name, default=False):
    return os.environ.get(name, "1" if default else "0").lower() in {"1", "true", "yes"}


def positive(name, default):
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ImproperlyConfigured(f"{name} must be positive")
    return value


DEBUG = flag("DEBUG", True)
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY and DEBUG:
    key_path = DATA_DIR / ".development-secret"
    try:
        with key_path.open("x") as f:
            f.write(secrets.token_urlsafe(64))
        key_path.chmod(0o600)
    except FileExistsError:
        pass
    SECRET_KEY = key_path.read_text().strip()
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("REPLACE_"):
    raise ImproperlyConfigured("Set SECRET_KEY to at least 50 random characters before deployment")

ALLOWED_HOSTS = [v.strip() for v in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if v.strip()]
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
site = urlsplit(SITE_URL)
if site.scheme not in {"http", "https"} or not site.hostname or site.path or site.query or site.fragment or site.username:
    raise ImproperlyConfigured("SITE_URL must be an absolute origin, e.g. https://ask.example.com")
if not DEBUG and site.scheme != "https":
    raise ImproperlyConfigured("Production SITE_URL must use HTTPS")
OWNER_NAME = os.environ.get("OWNER_NAME", "站长")[:40]
SITE_TITLE = os.environ.get("SITE_TITLE", f"{OWNER_NAME}的匿名提问箱")[:60]
TRUSTED_PROXY_CIDRS = tuple(ipaddress.ip_network(v.strip()) for v in os.environ.get("TRUSTED_PROXY_CIDRS", "").split(",") if v.strip())

INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "box",
]
MIDDLEWARE = [
    "box.middleware.BoundaryMiddleware", "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "box.context.site_context",
    ]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "questionbox.sqlite3",
    "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    "TEST": {"NAME": DATA_DIR / "test_questionbox.sqlite3"},
}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher", "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/owner/login/"
LOGIN_REDIRECT_URL = "/owner/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 12 * 60 * 60
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = [SITE_URL]
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 128 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 15

VISITOR_COOKIE = "__Host-qb-visitor" if not DEBUG else "qb-visitor"
VISITOR_COOKIE_SALT = "questionbox.visitor.v1"
VISITOR_COOKIE_AGE = 30 * 24 * 60 * 60
QUESTION_RATE_LIMIT_ENABLED = flag("QUESTION_RATE_LIMIT_ENABLED", True)
RATE_COOKIE_MINUTE = positive("RATE_COOKIE_MINUTE", 1)
RATE_COOKIE_HOUR = positive("RATE_COOKIE_HOUR", 10)
RATE_IP_HOUR = positive("RATE_IP_HOUR", 30)
RATE_SITE_HOUR = positive("RATE_SITE_HOUR", 300)
MAX_QUESTIONS = 500

SERVERCHAN_SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "").strip()

WECHAT_ENABLED = flag("WECHAT_ENABLED")
WECHAT_APPID = os.environ.get("WECHAT_APPID", "").strip()
WECHAT_APP_SECRET = os.environ.get("WECHAT_APP_SECRET", "").strip()
WECHAT_SESSION_DAYS = positive("WECHAT_SESSION_DAYS", 30)
WECHAT_REPLY_TEMPLATE_ID = os.environ.get("WECHAT_REPLY_TEMPLATE_ID", "").strip()
WECHAT_MINIPROGRAM_STATE = os.environ.get("WECHAT_MINIPROGRAM_STATE", "trial").strip()
try:
    WECHAT_REPLY_TEMPLATE_FIELDS = json.loads(os.environ.get("WECHAT_REPLY_TEMPLATE_FIELDS", "{}"))
except json.JSONDecodeError:
    raise ImproperlyConfigured("WECHAT_REPLY_TEMPLATE_FIELDS must be a JSON object") from None
if not isinstance(WECHAT_REPLY_TEMPLATE_FIELDS, dict):
    raise ImproperlyConfigured("WECHAT_REPLY_TEMPLATE_FIELDS must be a JSON object")
for field, value in WECHAT_REPLY_TEMPLATE_FIELDS.items():
    allowed = {"notice": r"thing\d+", "status": r"(?:thing|phrase)\d+", "time": r"time\d+", "date": r"date\d+"}
    if not isinstance(value, str) or value not in allowed or not re.fullmatch(allowed[value], field):
        raise ImproperlyConfigured("Invalid WeChat reply template field mapping")
if WECHAT_ENABLED and (not WECHAT_APPID or not WECHAT_APP_SECRET):
    raise ImproperlyConfigured("WECHAT_ENABLED requires WECHAT_APPID and WECHAT_APP_SECRET")
if WECHAT_MINIPROGRAM_STATE not in {"developer", "trial", "formal"}:
    raise ImproperlyConfigured("Invalid WECHAT_MINIPROGRAM_STATE")

EMAIL_ENABLED = flag("EMAIL_ENABLED")
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = flag("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = flag("EMAIL_USE_SSL")
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "")
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("Use TLS or SSL, not both")
if EMAIL_ENABLED and EMAIL_BACKEND.endswith("smtp.EmailBackend") and (not EMAIL_HOST or not DEFAULT_FROM_EMAIL):
    raise ImproperlyConfigured("Mail requires EMAIL_HOST and DEFAULT_FROM_EMAIL")

# A registered app may authorize a personal mailbox or its own Microsoft 365 tenant.
MS_GRAPH_CLIENT_ID = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
MS_GRAPH_TENANT_ID = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
MS_GRAPH_CLIENT_SECRET = os.environ.get("MS_GRAPH_CLIENT_SECRET", "").strip()
MS_GRAPH_ACCOUNT_TYPE = os.environ.get("MS_GRAPH_ACCOUNT_TYPE", "personal").strip()
if MS_GRAPH_ACCOUNT_TYPE not in {"personal", "organization"}:
    raise ImproperlyConfigured("MS_GRAPH_ACCOUNT_TYPE must be personal or organization")
for name, value in (("MS_GRAPH_CLIENT_ID", MS_GRAPH_CLIENT_ID), ("MS_GRAPH_TENANT_ID", MS_GRAPH_TENANT_ID)):
    if value:
        from uuid import UUID
        try:
            UUID(value)
        except ValueError:
            raise ImproperlyConfigured(f"{name} must be a UUID") from None

# Bark owner notifications (empty device key disables delivery).
BARK_DEVICE_KEY = os.environ.get("BARK_DEVICE_KEY", "").strip()
BARK_SERVER_URL = os.environ.get("BARK_SERVER_URL", "https://api.day.app").strip().rstrip("/")
bark_server = urlsplit(BARK_SERVER_URL)
if bark_server.scheme not in {"http", "https"} or not bark_server.hostname or bark_server.username or bark_server.password or bark_server.query or bark_server.fragment:
    raise ImproperlyConfigured("BARK_SERVER_URL must be an HTTP(S) server URL without credentials or query parameters")
