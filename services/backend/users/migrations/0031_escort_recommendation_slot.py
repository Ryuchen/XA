from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('users', '0030_registration_deposit_mode')]
    operations = [
        migrations.AddField(model_name='escortprofile', name='recommendation_slot',
            field=models.PositiveSmallIntegerField(null=True, blank=True, unique=True,
                choices=[(n, f'推荐位 {n}') for n in range(1, 5)])),
        migrations.AddConstraint(model_name='escortprofile', constraint=models.CheckConstraint(
            condition=models.Q(recommendation_slot__isnull=True) | models.Q(recommendation_slot__range=(1,4)),
            name='escort_recommendation_slot_valid')),
    ]
