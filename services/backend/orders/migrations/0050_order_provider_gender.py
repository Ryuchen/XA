from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('orders', '0049_backfill_benefit_clocks')]
    operations = [migrations.AddField(model_name='order', name='provider_gender',
        field=models.CharField(max_length=6, default='ANY', db_default='ANY', choices=[('ANY','不限'),('MALE','男陪'),('FEMALE','女陪')]))]
