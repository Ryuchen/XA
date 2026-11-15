import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0045_passrefundrepaymentaction')]

    operations = [
        migrations.RemoveConstraint(model_name='escortschedule', name='escort_schedule_period_valid'),
        migrations.AlterField(model_name='escortschedule', name='start_minute',
            field=models.PositiveSmallIntegerField(validators=[
                django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1439)])),
        migrations.AddConstraint(
            model_name='escortschedule',
            constraint=models.CheckConstraint(
                condition=(models.Q(start_minute__gte=0)
                           & models.Q(start_minute__lt=1440)
                           & models.Q(end_minute__gte=0)
                           & models.Q(end_minute__lte=1440)
                           & ~models.Q(start_minute=models.F('end_minute'))),
                name='escort_schedule_period_valid',
            ),
        ),
    ]
