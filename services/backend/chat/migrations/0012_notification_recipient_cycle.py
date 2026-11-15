from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0011_session_notification_destinations')]
    operations = [
        migrations.AddField(model_name=model, name=name, field=field)
        for model in ('chatnotificationoutbox', 'chatsessionnotificationoutbox')
        for name, field in (
            ('recipient_account_id_snapshot', models.PositiveBigIntegerField(blank=True, editable=False, null=True)),
            ('recipient_business_cycle', models.PositiveIntegerField(blank=True, editable=False, null=True)),
            ('account_skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        )
    ]
    operations += [
        migrations.AddField(model_name='chatmessage', name='notification_intent',
                            field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name='chatmessage', name='notification_recipient_id',
                            field=models.PositiveBigIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='chatmessage', name='notification_recipient_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
    ]
