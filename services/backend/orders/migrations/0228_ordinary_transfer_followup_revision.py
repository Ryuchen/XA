from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0227_referral_refund_funding')]
    operations = [migrations.CreateModel(name='OrdinaryTransferFollowupSettlementRevision', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('previous_plan', models.JSONField(blank=True, null=True)),
        ('previous_plan_hash', models.CharField(blank=True, default='', max_length=64)),
        ('settled_people', models.JSONField(default=list)), ('facts_hash', models.CharField(max_length=64)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('manifest', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='ordinary_followup_revisions', to='orders.ordersegmentsettlementmanifest')),
        ('rounding_transaction', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='wallet.transaction')),
        ('round', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            related_name='settlement_revision', to='orders.ordinarytransferfollowupround')),
    ])]
