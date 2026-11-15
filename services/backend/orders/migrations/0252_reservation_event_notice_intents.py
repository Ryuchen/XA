from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0251_reservation_status_notice_intent')]
    operations = [migrations.AddField(model_name='reservationstatuslog', name='notification_intents',
                                      field=models.JSONField(blank=True, editable=False, null=True))]
