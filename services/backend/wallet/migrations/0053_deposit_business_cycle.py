from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0052_withdrawrequest_business_cycle'),
                    ('users', '0052_escortprofile_deposit_business_cycle')]
    operations = [
        migrations.AddField(model_name='depositpaymentsnapshot', name='account_business_cycle',
            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
        migrations.AddField(model_name='cashdeposittopup', name='account_business_cycle',
            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
        migrations.AddField(model_name='refunddepositdeficit', name='account_business_cycle',
            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
    ]
