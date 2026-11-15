import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0020_aftersaleclosurenotice'), ('orders', '0256_application_business_cycles'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='AfterSaleOpenNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('history_index', models.PositiveIntegerField()),
        ('history_fingerprint', models.CharField(max_length=64)),
        ('case_opened_at', models.DateTimeField()),
        ('account_type', models.CharField(max_length=12)),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('legacy_user_id', models.PositiveBigIntegerField(blank=True, null=True)),
        ('title', models.CharField(max_length=100)),
        ('preview', models.CharField(max_length=255)),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('case', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='open_notice_intents', to='orders.aftersale')),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='club_accounts.clubaccount')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('case', 'history_index', 'account'),
                                                        name='aftersale_open_notice_once')]})]
