import uuid
from django.db import models
from django.utils import timezone


class WechatIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appid = models.CharField(max_length=64)
    openid = models.CharField(max_length=128)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["appid", "openid"], name="unique_wechat_identity")]


class WechatSession(models.Model):
    token_digest = models.CharField(primary_key=True, max_length=64)
    identity = models.ForeignKey(WechatIdentity, on_delete=models.CASCADE, related_name="sessions")
    expires_at = models.DateTimeField(db_index=True)


class Tag(models.Model):
    name = models.CharField(max_length=30, unique=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class Question(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(max_length=700)
    signature = models.CharField(max_length=30, blank=True)
    is_featured = models.BooleanField(default=False)
    tags = models.ManyToManyField(Tag, blank=True, related_name="questions")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    answer = models.TextField(blank=True)
    draft = models.TextField(blank=True)
    # A revision also distinguishes an intentionally empty draft from no draft.
    draft_revision = models.UUIDField(null=True, blank=True)
    version = models.PositiveIntegerField(default=0)
    answered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    answer_updated_at = models.DateTimeField(null=True, blank=True)
    wechat_identity = models.ForeignKey(WechatIdentity, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="questions")
    client_request_id = models.UUIDField(null=True, blank=True)
    submission_digest = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["wechat_identity", "client_request_id"],
                                              name="unique_wechat_submission")]

    @property
    def display_name(self):
        return self.signature or "匿名朋友"


class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="subscription")
    email = models.EmailField()
    subscribed_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    active = models.BooleanField(default=True)


class MailJob(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="jobs")
    event = models.CharField(max_length=12)
    version = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["subscription", "event", "version"], name="unique_mail_event")]


class ServerChanJob(models.Model):
    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="owner_notification")
    status = models.CharField(max_length=12, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class BarkJob(models.Model):
    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="bark_notification")
    status = models.CharField(max_length=12, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class RateBucket(models.Model):
    key = models.CharField(primary_key=True, max_length=80)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class WechatReminder(models.Model):
    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="wechat_reminder")
    active = models.BooleanField(default=True)
    subscribed_at = models.DateTimeField(default=timezone.now)


class WechatMessageJob(models.Model):
    reminder = models.OneToOneField(WechatReminder, on_delete=models.CASCADE, related_name="job")
    status = models.CharField(max_length=12, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
