from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0037_license_refund_vip')]

    operations = [
        migrations.AddField(model_name='kookbinding', name='business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='kookbindingchallenge', name='business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='kookbinding', name='cycle_online_base',
                            field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name='kookbinding', name='cycle_awarded_base',
                            field=models.PositiveBigIntegerField(default=0)),
    ]
