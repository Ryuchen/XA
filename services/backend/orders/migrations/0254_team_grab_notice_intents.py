from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0253_single_grab_notice_intents')]
    operations = [migrations.AddField(model_name='ordergrabteamroundstate', name='notification_intents',
                                      field=models.JSONField(blank=True, editable=False, null=True))]
