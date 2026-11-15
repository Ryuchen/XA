import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('support', '0002_supportcontactcard_wecom'), ('club_accounts', '0005_clubaccount_business_cycle')]
    operations = [migrations.AddField(model_name='supportcontactcard', name='staff_account',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                related_name='support_cards', to='club_accounts.clubaccount'))]
