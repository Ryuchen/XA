from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0014_providerreport_order_images')]
    operations = [
        migrations.RemoveConstraint(model_name='providerreport', name='uniq_provider_order_report'),
        migrations.AddConstraint(model_name='providerreport', constraint=models.UniqueConstraint(fields=('provider', 'order'), name='uniq_provider_order_report')),
    ]
