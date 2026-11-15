from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0012_notification_recipient_cycle')]
    operations = [
        migrations.AddField(model_name='chatsession', name='refresh_notification_intents',
                            field=models.JSONField(blank=True, default=list, editable=False)),
        migrations.AddField(model_name='chatsessionnotificationoutbox', name='intent_key',
                            field=models.CharField(blank=True, editable=False, max_length=32, null=True, unique=True)),
    ]
