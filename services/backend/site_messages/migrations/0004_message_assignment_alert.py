from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0003_message_site_msg_account_unread_idx_and_more')]
    operations = [migrations.AddField(model_name='message', name='assignment_alert', field=models.BooleanField(default=False))]
