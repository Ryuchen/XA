import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('stores', '0033_withdrawal_audit_scanner')]
    operations = [migrations.CreateModel(name='PlatformReferralFundingOperation', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ('request_id', models.UUIDField(unique=True)), ('order_id', models.PositiveBigIntegerField()),
        ('amount', models.PositiveIntegerField()), ('payload_digest', models.CharField(max_length=64)),
        ('target_refund_amount', models.PositiveIntegerField()),
        ('tenant_action_id', models.PositiveBigIntegerField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)), ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('rejected_at', models.DateTimeField(blank=True, null=True)),
        ('rejection_message', models.CharField(blank=True, default='', max_length=200)),
        ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='stores.platformaccount')),
        ('claim', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='stores.platformpassrepaymentclaim')),
    ], options={'constraints': [models.CheckConstraint(condition=models.Q(amount__gt=0), name='referral_funding_op_positive'),
        models.CheckConstraint(condition=(
            models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=True, rejection_message='')
            | models.Q(tenant_action_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, rejection_message='')
            | (models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=False)
               & ~models.Q(rejection_message=''))), name='referral_funding_op_result')]})]
