from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('stores', '0003_admin_login_handoff')]
    operations = [
        migrations.AddField(
            model_name='store',
            name='logo_url',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
