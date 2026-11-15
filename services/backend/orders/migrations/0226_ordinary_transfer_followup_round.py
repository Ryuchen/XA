from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0225_ordinary_transfer_addon_agreement')]
    operations = [
        migrations.CreateModel(name='OrdinaryTransferFollowupRound', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('sequence', models.PositiveIntegerField()), ('facts', models.JSONField()),
            ('facts_hash', models.CharField(max_length=64)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('execution', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='ordinary_transfer_followup_round', to='orders.negotiationexecution')),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='ordinary_transfer_followups', to='orders.order')),
            ('previous', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='ordinary_transfer_followup_successors', to='orders.negotiationexecution')),
            ('proposal', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='ordinary_transfer_followup', to='orders.negotiationproposal')),
        ])
    ]
