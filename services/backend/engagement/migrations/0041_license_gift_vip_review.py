from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('engagement', '0040_kook_license_points_review')]

    operations = [
        migrations.CreateModel(name='LicenseGiftVipDecision', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('business_cycle', models.PositiveIntegerField()),
            ('paid_units', models.PositiveBigIntegerField()),
            ('license_evidence', models.JSONField(default=dict)),
            ('decided_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
            ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                           related_name='license_gift_vip_decision', to='orders.order')),
        ]),
        migrations.CreateModel(name='LicenseGiftVipReview', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('request_id', models.UUIDField(unique=True)),
            ('platform_actor_id', models.UUIDField()),
            ('outcome', models.CharField(choices=[('ALLOW', '允许'), ('DENY', '不允许')], max_length=8)),
            ('reason', models.CharField(max_length=500)),
            ('vip_applied', models.BooleanField(default=False)),
            ('vip_recalculation_pending', models.BooleanField(default=False)),
            ('refunded_units', models.PositiveBigIntegerField(default=0)),
            ('skip_reason', models.CharField(blank=True, default='', max_length=24)),
            ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('decision', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='review', to='engagement.licensegiftvipdecision')),
        ]),
    ]
