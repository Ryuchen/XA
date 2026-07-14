import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0014_orderprovider_settlement_base'),
        ('support', '0002_supportcontactcard_wecom'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='support_contact',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='orders', to='support.supportcontactcard',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='support_contact_name_snapshot',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]
