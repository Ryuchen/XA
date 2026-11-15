from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0254_team_grab_notice_intents')]
    operations = [
        migrations.AddField(model_name='ordergrabsingleadmission', name='disqualification_notice_intents',
                            field=models.JSONField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='ordergrabteamdraft', name='disqualification_notice_intents',
                            field=models.JSONField(blank=True, editable=False, null=True)),
    ]
