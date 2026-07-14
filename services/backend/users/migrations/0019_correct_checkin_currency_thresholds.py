from django.db import migrations, models


def correct_checkin_thresholds(apps, schema_editor):
    """把早期按兴安币直接填写的门槛修正为人民币 1:10 换算后的兴安币账务值。"""
    Rule = apps.get_model('users', 'CheckinRuleConfig')
    Rule.objects.filter(daily_spend_required=1880).update(daily_spend_required=18800)
    Rule.objects.filter(makeup_card_spend_required=3880).update(makeup_card_spend_required=38800)


def reverse_checkin_thresholds(apps, schema_editor):
    Rule = apps.get_model('users', 'CheckinRuleConfig')
    Rule.objects.filter(daily_spend_required=18800).update(daily_spend_required=1880)
    Rule.objects.filter(makeup_card_spend_required=38800).update(makeup_card_spend_required=3880)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0018_checkingift_checkinruleconfig_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='checkinruleconfig',
            name='daily_spend_required',
            field=models.PositiveIntegerField(default=18800),
        ),
        migrations.AlterField(
            model_name='checkinruleconfig',
            name='makeup_card_spend_required',
            field=models.PositiveIntegerField(default=38800),
        ),
        migrations.RunPython(correct_checkin_thresholds, reverse_checkin_thresholds),
    ]
