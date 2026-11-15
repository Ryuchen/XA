from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0249_reservation_recipient_cycles')]
    operations = [
        migrations.AddField(model_name='reservation', name='invitation_notice_intent',
                            field=models.JSONField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='reservationseatevent', name='invitation_notice_intent',
                            field=models.JSONField(blank=True, editable=False, null=True)),
    ]
