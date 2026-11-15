from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('stores', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='store', name='currency_name',
            field=models.CharField(default='浪花', max_length=16),
        ),
    ]
