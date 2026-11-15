import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0046_addoncompletionnotice'), ('orders', '0256_application_business_cycles'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='AddonRefundProposalNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('role', models.CharField(max_length=8)), ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('segment_ids', models.JSONField(default=list)), ('status', models.CharField(default='PENDING', max_length=10)),
        ('note', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)), ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('proposal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonrefundproposal')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('proposal', 'account'), name='addon_refund_proposal_notice_once')]})]
