import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0012_reservationinvitenoticedelivery'),
                    ('orders', '0251_reservation_status_notice_intent')]
    operations = [migrations.CreateModel(name='ReservationStatusNoticeDelivery', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                         related_name='+', to='site_messages.message')),
        ('status_log', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='status_notice_delivery', to='orders.reservationstatuslog')),
        ('seat_event', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='status_notice_delivery', to='orders.reservationseatevent')),
    ], options={'constraints': [models.CheckConstraint(condition=(
        models.Q(status_log__isnull=False, seat_event__isnull=True)
        | models.Q(status_log__isnull=True, seat_event__isnull=False)), name='reservation_status_one_source')]})]
