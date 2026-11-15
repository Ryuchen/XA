from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0043_achievement_license_review')]
    operations = [migrations.AddField(model_name='kookpointsconfig', name='revision',
                                      field=models.PositiveIntegerField(default=0))]
