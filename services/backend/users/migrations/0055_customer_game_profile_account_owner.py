from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0054_checkin_notice_source'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='customergameprofile',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='game_profiles', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='customergameprofile',
            constraint=models.CheckConstraint(condition=models.Q(account__isnull=False) | models.Q(user__isnull=False),
                                              name='game_profile_has_owner'),
        ),
    ]
