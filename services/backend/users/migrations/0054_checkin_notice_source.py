from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0053_provider_pass_purchase_cycle')]
    operations = [
        migrations.AddField(model_name='checkinmonthprogress', name='full_attendance_notice_intent',
                            field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name='checkinmonthprogress', name='full_attendance_notice_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='checkinmonthprogress', name='full_attendance_notice_detail',
                            field=models.TextField(blank=True, default='', editable=False)),
    ]
