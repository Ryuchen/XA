from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0294_reservationclockreceipt'),
        ('wallet', '0063_widen_idempotency_scope'),
    ]

    operations = [
        migrations.CreateModel(
            name='SettlementClockEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_type', models.CharField(choices=[('SUBMISSION', '报单版本'), ('MANIFEST', '结算清单'), ('PERSON', '逐人结算'), ('ADDON', '加购逐段结算')], max_length=16)),
                ('subject_id', models.PositiveBigIntegerField()),
                ('operation', models.CharField(choices=[('SUBMIT', '报单提交'), ('REVIEW', '报单审核'), ('PLAN', '结算计划'), ('PERSON', '逐人结算'), ('ADDON', '加购逐段结算'), ('FINALIZE', '整单结算')], max_length=16)),
                ('observed_at', models.DateTimeField()),
                ('clock_evidence', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.order')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['order', 'subject_type', 'subject_id'], name='settlement_clock_order_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('subject_type', 'subject_id', 'operation'), name='settlement_clock_receipt_unique'),
                    models.CheckConstraint(condition=models.Q(subject_id__gt=0), name='settlement_clock_subject_positive'),
                ],
            },
        ),
    ]
