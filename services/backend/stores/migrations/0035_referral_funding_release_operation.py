import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('stores', '0034_referral_funding_operation')]
    operations = [migrations.CreateModel(name='PlatformReferralFundingReleaseOperation', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ('request_id', models.UUIDField(unique=True)), ('reason', models.CharField(max_length=200)),
        ('tenant_release_id', models.PositiveBigIntegerField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)), ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('rejected_at', models.DateTimeField(blank=True, null=True)),
        ('rejection_reason', models.CharField(blank=True, default='', max_length=200)),
        ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='stores.platformaccount')),
        ('funding_operation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='stores.platformreferralfundingoperation')),
    ], options={'constraints': [models.CheckConstraint(condition=(
        models.Q(tenant_release_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=True, rejection_reason='')
        | models.Q(tenant_release_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, rejection_reason='')
        | (models.Q(tenant_release_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=False)
           & ~models.Q(rejection_reason=''))), name='referral_release_op_result')]})]
