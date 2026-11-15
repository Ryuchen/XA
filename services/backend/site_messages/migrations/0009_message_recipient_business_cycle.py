from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0008_withdrawalnoticedelivery')]
    operations = [migrations.AddField(
        model_name='message', name='recipient_business_cycle',
        field=models.PositiveIntegerField(blank=True, editable=False, null=True),
    )]
