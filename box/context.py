from django.conf import settings
from .graph import notifications_enabled


def site_context(request):
    return {"owner_name": settings.OWNER_NAME, "site_title": settings.SITE_TITLE,
            "email_enabled": notifications_enabled()}
