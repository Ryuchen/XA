from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0212_orderprovidercompletionfact'), ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='OrderWorkStint', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('started_at', models.DateTimeField(blank=True, null=True)),
        ('released_at', models.DateTimeField(blank=True, null=True)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('entry_image', models.ImageField(blank=True, null=True, upload_to='reports/work_stints/')),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='work_stints', to='orders.order')),
        ('incoming', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='work_stint', to='orders.ordertransferseatevent')),
    ], options={'indexes': [models.Index(fields=['order', 'account'], name='work_stint_order_account_idx')]})]
