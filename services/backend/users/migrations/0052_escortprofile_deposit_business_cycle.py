from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0051_receipt_referral_release')]
    operations = [migrations.AddField(model_name='escortprofile', name='deposit_business_cycle',
        field=models.PositiveIntegerField(null=True, blank=True, editable=False))]
