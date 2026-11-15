import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0005_chatmessage_client_message_id_and_more'), ('club_accounts', '0005_clubaccount_business_cycle')]
    operations = [
        migrations.AddField(model_name='chatsession', name='claimed_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='claimed_chat_sessions', to='club_accounts.clubaccount', verbose_name='接待客服')),
        migrations.AddField(model_name='chatsession', name='claim_version', field=models.PositiveBigIntegerField(default=0, verbose_name='接待版本')),
        migrations.AddField(model_name='chatsession', name='claimed_at', field=models.DateTimeField(blank=True, null=True, verbose_name='接待时间')),
        migrations.CreateModel(name='ChatAssignmentEvent', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('action', models.CharField(max_length=16)),
            ('version', models.PositiveBigIntegerField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('previous', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('current', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('session', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignment_events', to='chat.chatsession')),
        ], options={'constraints': [models.UniqueConstraint(fields=('session', 'version'), name='chat_assignment_version_uniq')]}),
    ]
