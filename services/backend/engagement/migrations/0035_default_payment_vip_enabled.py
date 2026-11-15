from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0034_defaultpaymentpointssnapshot_defaultpaymentsource')]
    operations = [migrations.AddField(model_name='defaultpaymentpointssnapshot', name='vip_enabled',
                                      field=models.BooleanField(default=False))]
