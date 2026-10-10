import uuid
from django.db import models
from django.utils import timezone


class Question(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(max_length=500)
    signature = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    answer = models.TextField(blank=True)
    draft = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=0)
    answered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    answer_updated_at = models.DateTimeField(null=True, blank=True)
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
