import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0210_grab_clock_diagnostic'), ('support', '0003_contact_staff'),
                    ('club_accounts', '0005_clubaccount_business_cycle')]
    operations = [migrations.AddField(model_name='order', name='responsible_staff',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                related_name='responsible_orders', to='club_accounts.clubaccount'))]
