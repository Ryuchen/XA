from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0052_escortprofile_deposit_business_cycle')]

    operations = [
        migrations.AddField(model_name='providerpasspurchase', name='business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
    ]
