from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0015_escortprofile_voice_card'),
        ('wallet', '0013_alter_transaction_tx_type_pass_purchase'),
    ]

    operations = [
        migrations.AddField(
            model_name='escortprofile', name='pass_tier',
            field=models.CharField(blank=True, choices=[
                ('BLACK', '黑卡通行证'), ('GOLD', '金卡通行证'),
                ('SILVER', '银卡通行证'), ('BRONZE', '铜卡通行证'),
            ], db_index=True, default='', max_length=10),
        ),
        migrations.AddField(
            model_name='escortprofile', name='pass_expires_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name='ProviderPassPurchase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tier', models.CharField(choices=[
                    ('BLACK', '黑卡通行证'), ('GOLD', '金卡通行证'),
                    ('SILVER', '银卡通行证'), ('BRONZE', '铜卡通行证'),
                ], max_length=10)),
                ('days', models.PositiveSmallIntegerField()),
                ('daily_price', models.PositiveIntegerField()),
                ('original_amount', models.PositiveIntegerField()),
                ('discount_amount', models.PositiveIntegerField(default=0)),
                ('paid_amount', models.PositiveIntegerField()),
                ('starts_at', models.DateTimeField()),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pass_purchases', to='users.customuser')),
                ('transaction', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pass_purchases', to='wallet.transaction')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
