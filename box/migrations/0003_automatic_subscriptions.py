import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("box", "0002_serverchanjob")]

    operations = [
        # Existing confirmed subscribers keep their subscription date. Legacy unconfirmed
        # subscribers can still use their original confirmation link; no bulk mail is queued.
        migrations.RenameField(model_name="subscription", old_name="verified_at", new_name="subscribed_at"),
        migrations.AlterField(
            model_name="subscription", name="subscribed_at",
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, null=True),
        ),
    ]
