import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('site_messages', '0007_manualmessagecampaign_manualmessagerecipient'),
        ('wallet', '0054_transaction_deposit_cycle'),
    ]

    operations = [
        migrations.CreateModel(
            name='WithdrawalNoticeDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=100, unique=True)),
                ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
                ('platform_event', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.withdrawalplatformevent')),
                ('withdrawal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.withdrawrequest')),
            ],
        ),
    ]
