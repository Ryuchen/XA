from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0081_evaluation_reply_author')]
    operations = [
        migrations.AddField(model_name='orderrefund', name='negotiated_allocation',
                            field=models.JSONField(blank=True, default=list)),
    ]
