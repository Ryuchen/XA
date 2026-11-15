from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0010_chatsessionnotificationoutbox')]

    operations = [
        migrations.AddField(
            model_name='chatsessionnotificationoutbox',
            name='operators_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='chatsessionnotificationoutbox',
            name='account_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
