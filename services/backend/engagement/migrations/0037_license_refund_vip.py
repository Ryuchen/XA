import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0036_default_payment_vip_identity'), ('orders', '0236_addon_points_handover_snapshot')]

    operations = [
        migrations.CreateModel(name='LicenseRefundVipDecision', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('business_cycle', models.PositiveIntegerField()),
            ('paid_units', models.PositiveBigIntegerField()),
            ('refunded_units', models.PositiveBigIntegerField()),
            ('payment_vip_enabled', models.BooleanField()),
            ('confirmation_vip_enabled', models.BooleanField()),
            ('outcome', models.CharField(choices=[('ALLOW', '允许'), ('DENY', '不允许'), ('UNKNOWN', '待核对')], max_length=16)),
            ('license_evidence', models.JSONField(default=dict)),
            ('decided_at', models.DateTimeField()),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
            ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='license_refund_vip_decision', to='orders.order')),
        ]),
        migrations.CreateModel(name='LicenseRefundVipReview', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('request_id', models.UUIDField(unique=True)),
            ('platform_actor_id', models.UUIDField()),
            ('outcome', models.CharField(choices=[('ALLOW', '允许'), ('DENY', '不允许')], max_length=8)),
            ('reason', models.CharField(max_length=500)),
            ('vip_applied', models.BooleanField(default=False)),
            ('vip_recalculation_pending', models.BooleanField(default=False)),
            ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('decision', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='review', to='engagement.licenserefundvipdecision')),
        ]),
    ]
