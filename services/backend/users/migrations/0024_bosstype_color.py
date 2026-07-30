from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0023_escortprofile_service_items'),
    ]

    operations = [
        migrations.AddField(
            model_name='bosstype',
            name='color',
            field=models.CharField(blank=True, default='', max_length=9),
        ),
    ]
