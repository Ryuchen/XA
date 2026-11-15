from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('chat', '0013_session_refresh_intents'), ('site_messages', '0042_staffstartnotice')]
    operations = [
        migrations.AddField(model_name='chatassignmentevent', name='transfer_notice_pending',
            field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name='chatassignmentevent', name='transfer_notice_cycle',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='chatassignmentevent', name='transfer_notice_message',
            field=models.OneToOneField(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
        migrations.AddField(model_name='chatassignmentevent', name='transfer_notice_completed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='chatassignmentevent', name='transfer_notice_error',
            field=models.CharField(blank=True, default='', editable=False, max_length=255)),
    ]
