import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0009_chatassignmentevent_acting_as_supervisor_and_more')]

    operations = [
        migrations.CreateModel(
            name='ChatSessionNotificationOutbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(db_index=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('last_error', models.CharField(blank=True, default='', max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='state_notifications', to='chat.chatsession')),
            ],
            options={'indexes': [models.Index(fields=['completed_at', 'next_attempt_at', 'id'],
                                               name='chat_state_outbox_due_idx')]},
        ),
    ]
