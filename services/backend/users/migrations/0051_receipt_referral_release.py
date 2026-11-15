from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0050_receipt_referral_usage')]

    operations = [
        migrations.AddField(
            model_name='passreceiptusagestate', name='referral_released_units',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RemoveConstraint(model_name='passreceiptusageevent', name='receipt_usage_event_kind'),
        migrations.AddConstraint(
            model_name='passreceiptusageevent',
            constraint=models.CheckConstraint(
                condition=models.Q(kind__in=['PASS', 'ORDER', 'RELEASE', 'REFERRAL', 'REFERRAL_RELEASE']),
                name='receipt_usage_event_kind',
            ),
        ),
    ]
