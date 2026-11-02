from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0080_fix_pending_default_kook_text')]

    operations = [
        migrations.AddField(
            model_name='evaluation', name='reply_author',
            field=models.CharField(blank=True, choices=[('', '来源未知'), ('PROVIDER', '陪玩'), ('STAFF', '客服')], default='', max_length=12),
        ),
    ]
