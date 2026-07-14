import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0014_customuser_avatar')]

    operations = [
        migrations.AddField(
            model_name='escortprofile', name='voice_card',
            field=models.FileField(
                blank=True, null=True, upload_to='escorts/voice_cards/',
                validators=[django.core.validators.FileExtensionValidator(
                    allowed_extensions=['mp3', 'm4a', 'wav', 'aac', 'ogg'],
                )],
            ),
        ),
    ]
