from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0004_message_assignment_alert'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.AlterField(
        model_name='message', name='recipient',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                related_name='messages', to=settings.AUTH_USER_MODEL),
    )]
