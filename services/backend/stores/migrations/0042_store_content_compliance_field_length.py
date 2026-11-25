from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0041_publish_builtin_compliance_rules'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storecontentcompliancereceipt',
            name='field_name',
            field=models.CharField(max_length=40),
        ),
    ]
