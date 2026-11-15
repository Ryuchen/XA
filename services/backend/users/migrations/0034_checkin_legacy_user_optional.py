from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0033_escortprofile_dispatch_notification_preferences'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(model_name='checkinrecord', name='user', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
            related_name='checkin_records', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='checkinmonthprogress', name='user', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
            related_name='checkin_month_progresses', to=settings.AUTH_USER_MODEL)),
    ]
