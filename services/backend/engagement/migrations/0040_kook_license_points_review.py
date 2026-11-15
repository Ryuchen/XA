from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('engagement', '0039_provider_naming_business_cycle'),
                    ('console', '0004_multi_role_and_audit_log')]

    operations = [
        migrations.CreateModel(name='KookLicensePointsReview', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('request_id', models.UUIDField(unique=True)),
            ('platform_actor_id', models.UUIDField()),
            ('outcome', models.CharField(choices=[('ALLOW', '允许'), ('DENY', '不允许')], max_length=8)),
            ('evidence_digest', models.CharField(max_length=64)),
            ('reason', models.CharField(max_length=500)),
            ('awarded_points', models.PositiveBigIntegerField(default=0)),
            ('credited_milliseconds', models.PositiveBigIntegerField(default=0)),
            ('skip_reason', models.CharField(blank=True, default='', max_length=24)),
            ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('points_ledger', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                  related_name='+', to='engagement.pointledger')),
            ('source', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                            related_name='kook_license_review', to='console.adminauditlog')),
        ]),
    ]
