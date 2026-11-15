from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0051_reportapprovalnotice')]
    operations = [
        migrations.AddField(model_name='reportapprovalnotice', name='kind',
                            field=models.CharField(default='APPROVED', max_length=10)),
        migrations.AddField(model_name='reportapprovalnotice', name='detail',
                            field=models.CharField(blank=True, default='', max_length=300)),
    ]
