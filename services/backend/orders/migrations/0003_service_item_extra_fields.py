# Generated for service item extra fields (cover/images/highlights)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_order_state_machine'),
    ]

    operations = [
        migrations.AddField(
            model_name='serviceitem',
            name='cover_url',
            field=models.URLField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='serviceitem',
            name='images',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='serviceitem',
            name='highlights',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
