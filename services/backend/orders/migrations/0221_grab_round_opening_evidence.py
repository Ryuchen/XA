import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0220_negotiation_rounding_transaction')]

    operations = [migrations.CreateModel(
        name='OrderGrabRoundOpeningEvidence',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('state', models.CharField(choices=[('PENDING', '待观察提交'), ('PREPARED', '待观察激活'), ('OBSERVED', '已观察提交'), ('REVIEW', '待核对')], default='PENDING', max_length=12)),
            ('lower_at', models.DateTimeField()),
            ('upper_at', models.DateTimeField(blank=True, null=True)),
            ('clock_evidence', models.JSONField()),
            ('activation_lower_at', models.DateTimeField(blank=True, null=True)),
            ('activation_upper_at', models.DateTimeField(blank=True, null=True)),
            ('activation_clock_evidence', models.JSONField(blank=True, null=True)),
            ('reason', models.CharField(blank=True, default='', max_length=255)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('round', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='opening_evidence', to='orders.ordergrabround')),
        ],
    ),
        migrations.AddField(model_name='ordergrabreceiptprobe', name='opening_prepared_observed',
            field=models.BooleanField(default=False, editable=False)),
        migrations.RemoveConstraint(model_name='ordergrabreceiptprobe', name='grab_receipt_state_valid'),
        migrations.RemoveConstraint(model_name='ordergrabreceiptprobe', name='grab_receipt_evidence_valid'),
        migrations.AlterField(model_name='ordergrabreceiptprobe', name='state', field=models.CharField(
            choices=[('PENDING', '待确认'), ('WAIT_OPENING', '等待开池证据'), ('BEFORE', '证明截止前'),
                     ('AFTER', '证明截止后'), ('REVIEW', '待核对')], default='PENDING', max_length=16)),
        migrations.AddConstraint(model_name='ordergrabreceiptprobe', constraint=models.CheckConstraint(
            condition=models.Q(state__in=['PENDING', 'WAIT_OPENING', 'BEFORE', 'AFTER', 'REVIEW']), name='grab_receipt_state_valid')),
        migrations.AddConstraint(model_name='ordergrabreceiptprobe', constraint=models.CheckConstraint(condition=(
            models.Q(state='PENDING', upper_observed_at__isnull=True, review_reason='')
            | models.Q(state__in=['WAIT_OPENING', 'BEFORE', 'AFTER'], upper_observed_at__isnull=False, review_reason='')
            | (models.Q(state='REVIEW') & ~models.Q(review_reason=''))), name='grab_receipt_evidence_valid')),
    ]
