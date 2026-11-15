from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('engagement', '0032_license_consumption_points_review'),
        ('orders', '0185_orderaddonconsumption'),
    ]
    operations = [migrations.CreateModel(
        name='LicenseAddonBenefitsReview',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('request_id', models.UUIDField(unique=True)),
            ('platform_actor_id', models.UUIDField()),
            ('vip_outcome', models.CharField(blank=True, default='', max_length=8)),
            ('points_outcome', models.CharField(blank=True, default='', max_length=8)),
            ('reason', models.CharField(max_length=500)),
            ('vip_applied', models.BooleanField(default=False)),
            ('vip_recalculation_pending', models.BooleanField(default=False)),
            ('awarded_points', models.PositiveBigIntegerField(default=0)),
            ('refunded_principal_at_award', models.PositiveBigIntegerField(default=0)),
            ('skip_reason', models.CharField(blank=True, default='', max_length=24)),
            ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('consumption', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='license_review', to='orders.orderaddonconsumption')),
            ('points_ledger', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='+', to='engagement.pointledger')),
        ],
    )]
