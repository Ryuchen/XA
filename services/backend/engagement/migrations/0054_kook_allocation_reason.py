from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0053_kook_online_point_allocation')]
    operations = [migrations.AddField(model_name='kookonlinepointallocation', name='review_reason',
                                      field=models.CharField(blank=True, default='', max_length=500))]
