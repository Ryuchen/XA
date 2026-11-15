import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0239_points_zero_followup'), ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='OrderReferralCycleSnapshot', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('business_cycle', models.PositiveIntegerField()),
        ('inviter', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            related_name='referral_cycle_snapshot', to='orders.order')),
    ])]
