from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('engagement', '0042_achievement_license_event')]
    operations = [migrations.CreateModel(name='AchievementLicenseReview', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('request_id', models.UUIDField(unique=True)), ('platform_actor_id', models.UUIDField()),
        ('outcome', models.CharField(max_length=8)), ('reason', models.CharField(max_length=500)),
        ('granted_ids', models.JSONField(default=list)), ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
        ('event', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            related_name='review', to='engagement.achievementlicenseevent')),
    ])]
