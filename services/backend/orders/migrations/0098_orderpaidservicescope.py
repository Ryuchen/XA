from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0097_orderprestarthandoff')]

    operations = [
        migrations.CreateModel(
            name='OrderPaidServiceScope',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='paid_service_scope', to='orders.order')),
            ],
        ),
    ]
