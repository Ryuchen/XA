from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0255_grab_disqualification_notice_intents')]
    operations = [
        migrations.AddField(model_name='orderapplication', name='applicant_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='orderapplication', name='partner_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
    ]
