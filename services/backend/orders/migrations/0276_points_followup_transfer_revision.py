from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0275_reservation_seat_event_cycle')]

    operations = [
        migrations.CreateModel(
            name='PointsFollowupTransferRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('snapshot', models.JSONField()),
                ('evidence_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.order')),
                ('transfer', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderoriginalsharetransfer')),
                ('first_posting', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsnegotiationposting')),
                ('second_posting', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsnegotiationposting')),
                ('followup_revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderpointstransferfollowupfundingrevision')),
            ],
        ),
    ]
