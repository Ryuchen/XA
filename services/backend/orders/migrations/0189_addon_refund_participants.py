from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0188_addon_handover_funding_release')]

    operations = [
        migrations.RemoveConstraint(model_name='orderaddonrefund', name='addon_refund_burden_balanced'),
        migrations.RemoveConstraint(model_name='orderaddonrefund', name='addon_refund_wallet_valid'),
        migrations.RemoveConstraint(model_name='orderaddonrefundearningdeduction', name='addon_refund_earning_unique'),
        migrations.AddField(model_name='orderaddonrefund', name='rounding_surplus',
                            field=models.PositiveIntegerField(default=0)),
        migrations.CreateModel(name='OrderAddonRefundParticipant', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('basis', models.PositiveBigIntegerField()),
            ('burden', models.PositiveBigIntegerField()),
            ('excess_burden', models.PositiveBigIntegerField(default=0)),
            ('share_plan', models.JSONField()),
            ('addon_pending_deduction', models.PositiveBigIntegerField(default=0)),
            ('main_pending_deduction', models.PositiveBigIntegerField(default=0)),
            ('wallet_deduction', models.PositiveBigIntegerField(default=0)),
            ('deposit_deduction', models.PositiveBigIntegerField(default=0)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('proposal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='participants', to='orders.orderaddonrefundproposal')),
            ('provider_transaction', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.transaction')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=('proposal', 'account'), name='addon_refund_participant_unique'),
            models.CheckConstraint(condition=models.Q(basis__gt=0, burden__gt=0), name='addon_refund_participant_positive'),
        ]}),
        migrations.AddField(model_name='orderaddonrefundearningdeduction', name='participant',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='earning_deductions', to='orders.orderaddonrefundparticipant')),
        migrations.AddField(model_name='orderaddonrefundearningdeduction', name='segment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderparticipationsegment')),
        migrations.AddConstraint(model_name='orderaddonrefundearningdeduction', constraint=models.UniqueConstraint(
            fields=('refund', 'payment', 'participant', 'segment'), name='addon_refund_earning_person_unique')),
        migrations.AddConstraint(model_name='orderaddonrefund', constraint=models.CheckConstraint(condition=models.Q(
            amount=models.F('addon_pending_deduction') + models.F('main_pending_deduction') +
            models.F('wallet_deduction') + models.F('deposit_deduction') - models.F('rounding_surplus')),
            name='addon_refund_burden_balanced')),
    ]
