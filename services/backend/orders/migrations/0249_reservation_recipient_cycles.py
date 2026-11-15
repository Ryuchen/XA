from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0248_grab_invitee_business_cycle')]
    operations = [
        migrations.AddField(model_name='reservation', name='customer_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='reservation', name='provider_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='reservationseat', name='provider_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
    ]
