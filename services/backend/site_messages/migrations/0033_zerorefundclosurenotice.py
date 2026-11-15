import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0032_addonrefundnotice'), ('orders', '0256_application_business_cycles'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='ZeroRefundClosureNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('source_kind', models.CharField(max_length=16)),
        ('source_id', models.PositiveBigIntegerField()),
        ('history_index', models.PositiveIntegerField()),
        ('history_fingerprint', models.CharField(max_length=64)),
        ('role', models.CharField(max_length=8)),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('title', models.CharField(max_length=100)),
        ('preview', models.CharField(max_length=255)),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='club_accounts.clubaccount')),
        ('case', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='zero_notice_intents', to='orders.aftersale')),
        ('proposal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='orders.negotiationproposal')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('case', 'history_index', 'account'),
                                                        name='zero_closure_notice_once')]})]
