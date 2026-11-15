from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0213_orderworkstint'), ('club_accounts', '0001_initial')]
    operations = [
        migrations.CreateModel(name='OrderWorkOriginalShare', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('accepted_at', models.DateTimeField()),
            ('amount', models.PositiveBigIntegerField()),
            ('stint', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='original_share_fact', to='orders.orderworkstint')),
        ]),
        migrations.CreateModel(name='OrderWorkOriginalTransfer', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('original_before', models.PositiveBigIntegerField()),
            ('retained', models.PositiveBigIntegerField()),
            ('transferred', models.PositiveBigIntegerField()),
            ('agreement', models.CharField(max_length=500)),
            ('created_at', models.DateTimeField()),
            ('event', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='work_original_transfer', to='orders.ordertransferseatevent')),
            ('previous', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_out', to='orders.orderworkoriginalshare')),
            ('incoming', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_in', to='orders.orderworkoriginalshare')),
            ('operator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ], options={'constraints': [models.CheckConstraint(condition=models.Q(original_before=models.F('retained') + models.F('transferred')), name='work_original_transfer_conserved')]}),
    ]
