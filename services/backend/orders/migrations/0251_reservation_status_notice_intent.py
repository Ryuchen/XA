from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0250_reservation_invitation_notice_intent')]
    operations = [
        migrations.AddField(model_name='reservationstatuslog', name='status_notice_intent',
                            field=models.JSONField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='reservationseatevent', name='status_notice_intent',
                            field=models.JSONField(blank=True, editable=False, null=True)),
    ]
