import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0033_zerorefundclosurenotice'), ('orders', '0256_application_business_cycles'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='AddonHandoverResultNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('role', models.CharField(max_length=8)),
        ('result', models.CharField(max_length=12)),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('title', models.CharField(max_length=100)),
        ('preview', models.CharField(max_length=255)),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='club_accounts.clubaccount')),
        ('handover', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='result_notice_intents', to='orders.orderaddonhandover')),
        ('segment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='orders.orderparticipationsegment')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('handover', 'account'), name='addon_handover_notice_once')]})]
