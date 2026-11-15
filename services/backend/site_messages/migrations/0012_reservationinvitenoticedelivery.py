import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0011_checkinnoticedelivery'),
                    ('orders', '0250_reservation_invitation_notice_intent')]
    operations = [migrations.CreateModel(
        name='ReservationInviteNoticeDelivery',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                             related_name='+', to='site_messages.message')),
            ('reservation', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='invitation_notice_delivery', to='orders.reservation')),
            ('seat_event', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='invitation_notice_delivery', to='orders.reservationseatevent')),
        ], options={'constraints': [models.CheckConstraint(condition=(
            models.Q(reservation__isnull=False, seat_event__isnull=True)
            | models.Q(reservation__isnull=True, seat_event__isnull=False)), name='reservation_notice_one_source')]},
    )]
