import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0053_deposit_business_cycle')]
    operations = [
        migrations.AddField(model_name='transaction', name='deposit_owner',
            field=models.ForeignKey(to='club_accounts.clubaccount', on_delete=django.db.models.deletion.PROTECT,
                null=True, blank=True, related_name='+', editable=False)),
        migrations.AddField(model_name='transaction', name='deposit_business_cycle',
            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
        migrations.AddField(model_name='transaction', name='deposit_pair',
            field=models.OneToOneField(to='wallet.transaction', on_delete=django.db.models.deletion.PROTECT,
                null=True, blank=True, related_name='+', editable=False)),
    ]
