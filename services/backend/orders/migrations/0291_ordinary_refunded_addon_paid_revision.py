from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0290_ordinary_addon_paid_refund')]
    operations = [migrations.CreateModel(name='OrdinaryRefundedAddonPaidRevision', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('snapshot', models.JSONField()),
        ('evidence_hash', models.CharField(max_length=64)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('settlement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderpointssettlement')),
        ('refund', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonrefund')),
        ('run', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonfundingreleaserun')),
    ])]
