from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('users', '0047_bossregistrationchallenge')]

    operations = [
        migrations.AddField(
            model_name='passrefundrepaymentreceipt', name='usage_version',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='PassReceiptUsageState',
            fields=[
                ('receipt', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, primary_key=True,
                    related_name='usage_state', serialize=False, to='users.passrefundrepaymentreceipt')),
                ('version', models.PositiveBigIntegerField(default=0)),
                ('pass_units', models.PositiveBigIntegerField(default=0)),
                ('order_units', models.PositiveBigIntegerField(default=0)),
                ('released_units', models.PositiveBigIntegerField(default=0)),
                ('chain_hash', models.CharField(default='0' * 64, max_length=64)),
            ],
            options={'constraints': [models.CheckConstraint(
                condition=models.Q(released_units__lte=models.F('order_units')), name='receipt_usage_release_lte_order')]},
        ),
        migrations.CreateModel(
            name='PassReceiptUsageEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveBigIntegerField()),
                ('request_key', models.CharField(max_length=160)),
                ('kind', models.CharField(max_length=16)),
                ('allocation_key', models.CharField(max_length=160)),
                ('amount', models.PositiveBigIntegerField()),
                ('source_digest', models.CharField(max_length=64)),
                ('previous_hash', models.CharField(max_length=64)),
                ('event_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('state', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name='events', to='users.passreceiptusagestate')),
            ],
            options={'constraints': [
                models.UniqueConstraint(fields=('state', 'version'), name='receipt_usage_event_version_uniq'),
                models.UniqueConstraint(fields=('state', 'request_key'), name='receipt_usage_event_request_uniq'),
                models.CheckConstraint(condition=models.Q(version__gt=0, amount__gt=0), name='receipt_usage_event_positive'),
                models.CheckConstraint(condition=models.Q(kind__in=['PASS', 'ORDER', 'RELEASE']), name='receipt_usage_event_kind'),
            ]},
        ),
    ]
