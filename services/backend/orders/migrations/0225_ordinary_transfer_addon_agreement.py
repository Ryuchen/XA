from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0224_grab_ranking_snapshot')]
    operations = [
        migrations.CreateModel(
            name='OrdinaryTransferAddonAgreement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('snapshot', models.JSONField()),
                ('snapshot_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('proposal', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='ordinary_transfer_addon_agreement', to='orders.negotiationproposal')),
            ],
        ),
    ]
