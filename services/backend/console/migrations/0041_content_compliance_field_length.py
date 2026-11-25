from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0040_content_compliance_permissions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contentcompliancereceipt',
            name='field_name',
            field=models.CharField(max_length=40),
        ),
    ]
