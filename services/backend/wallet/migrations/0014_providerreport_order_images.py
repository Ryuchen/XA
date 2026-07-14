from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0014_orderprovider_settlement_base'), ('wallet', '0013_alter_transaction_tx_type_pass_purchase')]

    operations = [
        migrations.AddField(model_name='providerreport', name='order', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='provider_reports', to='orders.order')),
        migrations.AddField(model_name='providerreport', name='entry_image', field=models.ImageField(blank=True, null=True, upload_to='reports/entry/')),
        migrations.AddField(model_name='providerreport', name='completion_image', field=models.ImageField(blank=True, null=True, upload_to='reports/completion/')),
        migrations.AlterField(model_name='providerreport', name='status', field=models.CharField(choices=[('DRAFT', '待提交'), ('PENDING', '待审核'), ('APPROVED', '已通过'), ('REJECTED', '已驳回')], db_index=True, default='PENDING', max_length=20)),
        migrations.CreateModel(
            name='ProviderReportImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='reports/results/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='result_images', to='wallet.providerreport')),
            ],
            options={'ordering': ['id']},
        ),
        migrations.AddConstraint(model_name='providerreport', constraint=models.UniqueConstraint(condition=models.Q(('order__isnull', False)), fields=('provider', 'order'), name='uniq_provider_order_report')),
    ]
