from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('engagement', '0038_kook_membership_cycle'),
        ('orders', '0247_order_gift_provider_cycle'),
    ]

    operations = [
        migrations.RemoveConstraint(model_name='providernaming', name='unique_provider_sponsor_naming'),
        migrations.AddField(model_name='providernaming', name='provider_business_cycle',
                            field=models.PositiveIntegerField(editable=False)),
        migrations.AddConstraint(model_name='providernaming', constraint=models.UniqueConstraint(
            fields=('provider', 'sponsor', 'provider_business_cycle'), name='unique_provider_sponsor_cycle_naming')),
    ]
