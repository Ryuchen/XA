from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('stores', '0035_referral_funding_release_operation')]
    operations = [
        migrations.CreateModel(
            name='StoreDomain',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hostname', models.CharField(max_length=253, unique=True)),
                ('active', models.BooleanField(default=True)),
                ('revision', models.PositiveBigIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                            related_name='domains', to='stores.store')),
            ],
        ),
    ]
