from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0020_customergameprofile'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customuser',
            name='openid',
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
    ]
