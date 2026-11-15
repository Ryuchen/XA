from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0231_referral_funding_release')]
    operations = [
        migrations.AddField(model_name='aftersale', name='affected_segment_ids',
                            field=models.JSONField(blank=True, default=None, null=True)),
        migrations.AddField(model_name='negotiationproposal', name='affected_segment_ids',
                            field=models.JSONField(blank=True, default=None, null=True)),
    ]
