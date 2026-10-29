from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("box", "0006_question_text_700")]

    operations = [
        migrations.AddField(
            model_name="question",
            name="draft_revision",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
