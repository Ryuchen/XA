import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0238_addon_followup_funding_snapshot'), ('club_accounts', '0001_initial')]

    operations = [migrations.CreateModel(
        name='PointsZeroFollowupTermination',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('request_id', models.CharField(max_length=64)),
            ('accepted_at', models.DateTimeField()),
            ('funding_snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('terminated_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderprovider')),
            ('operator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='points_zero_followup', to='orders.order')),
            ('prior_posting', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsnegotiationposting')),
            ('proposal', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='points_zero_followup', to='orders.negotiationproposal')),
            ('segment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderparticipationsegment')),
        ],
    )]
