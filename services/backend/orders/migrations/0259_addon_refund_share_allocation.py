from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0258_addon_participant_zero_wallet_evidence')]

    operations = [migrations.CreateModel(
        name='OrderAddonRefundShareAllocation',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('refund_basis', models.PositiveBigIntegerField(default=0)),
            ('earning_deduction', models.PositiveBigIntegerField(default=0)),
            ('refund', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                         related_name='share_allocations', to='orders.orderaddonrefund')),
            ('payment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                          related_name='+', to='orders.orderaddonpayment')),
            ('participant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='+', to='orders.orderaddonrefundparticipant')),
            ('share', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                        related_name='+', to='orders.orderaddonhandovershare')),
        ],
        options={'constraints': [
            models.UniqueConstraint(fields=('refund', 'participant', 'share'), name='addon_refund_share_person_unique'),
            models.CheckConstraint(condition=models.Q(('refund_basis__gt', 0), ('earning_deduction__gt', 0),
                                                       _connector='OR'), name='addon_refund_share_nonzero'),
        ]},
    )]
