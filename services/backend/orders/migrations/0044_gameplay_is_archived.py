from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('orders', '0043_gameplay_serviceitem_details_html')]
    operations = [migrations.AddField(model_name='gameplay', name='is_archived', field=models.BooleanField(default=False, db_index=True))]
