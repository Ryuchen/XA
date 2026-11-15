from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('engagement', '0031_remove_lotterypitystate_lottery_pity_below_guarantee_and_more')]
    operations = [
        migrations.CreateModel(
            name='LicenseConsumptionPointsReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.UUIDField(unique=True)),
                ('platform_actor_id', models.UUIDField()),
                ('outcome', models.CharField(choices=[('ALLOW', '允许补发'), ('DENY', '不予发放')], max_length=8)),
                ('reason', models.CharField(max_length=500)),
                ('awarded_points', models.PositiveBigIntegerField(default=0)),
                ('refunded_principal_at_award', models.PositiveBigIntegerField(default=0)),
                ('skip_reason', models.CharField(blank=True, default='', max_length=24)),
                ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('decision', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='review', to='engagement.licenseconsumptionpointsdecision')),
                ('points_ledger', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='+', to='engagement.pointledger')),
            ],
        ),
    ]
