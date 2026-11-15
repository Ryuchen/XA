from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0252_reservation_event_notice_intents')]
    operations = [migrations.AddField(model_name='ordergrabsingleroundstate', name='notification_intents',
                                      field=models.JSONField(blank=True, editable=False, null=True))]
