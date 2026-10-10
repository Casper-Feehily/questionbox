from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
import requests

from .graph import GraphDeliveryError, access_token


def recipients(addresses):
    return [{"emailAddress": {"address": parseaddr(value)[1]}} for value in addresses]


class EmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for email in email_messages:
            if not email.recipients():
                continue
            try:
                if email.attachments:
                    raise GraphDeliveryError("此通知后端不发送附件。")
                message = {"subject": email.subject,
                           "body": {"contentType": "HTML" if email.content_subtype == "html" else "Text", "content": email.body},
                           "toRecipients": recipients(email.to), "ccRecipients": recipients(email.cc),
                           "bccRecipients": recipients(email.bcc)}
                if email.reply_to:
                    message["replyTo"] = recipients(email.reply_to)
                # /me sends as the authorized mailbox, never as a caller-supplied From address.
                payload = {"message": message, "saveToSentItems": True}
                response = None
                for attempt in range(2):
                    token = access_token(force_refresh=bool(attempt))
                    response = requests.post(
                        "https://graph.microsoft.com/v1.0/me/sendMail", json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=settings.EMAIL_TIMEOUT, allow_redirects=False)
                    if response.status_code != 401:
                        break
                if response.status_code != 202:
                    raise GraphDeliveryError("Microsoft 未接受这封邮件，请检查邮箱授权、邮箱服务或发送额度。")
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return sent
