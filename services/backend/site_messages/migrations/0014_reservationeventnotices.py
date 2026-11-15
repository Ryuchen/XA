import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0013_reservationstatusnoticedelivery'),
                    ('orders', '0252_reservation_event_notice_intents')]
    operations = [
        migrations.CreateModel(name='ReservationEventNoticeBatch', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('completed_at', models.DateTimeField(blank=True, null=True)),
            ('status_log', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='event_notice_batch', to='orders.reservationstatuslog')),
        ]),
        migrations.CreateModel(name='ReservationEventNoticeDelivery', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('position', models.PositiveIntegerField()),
            ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                             related_name='+', to='site_messages.message')),
            ('status_log', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='event_notice_deliveries', to='orders.reservationstatuslog')),
        ], options={'constraints': [models.UniqueConstraint(fields=('status_log', 'position'),
                                                             name='reservation_event_notice_once')]}),
    ]
