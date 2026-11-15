from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('audition', '0004_auditionlink_boss_account_and_more')]
    operations = [
        migrations.AddField(model_name='auditionsignup', name='applicant_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='auditionsignup', name='audit_notice_intent',
                            field=models.JSONField(blank=True, editable=False, null=True)),
    ]
