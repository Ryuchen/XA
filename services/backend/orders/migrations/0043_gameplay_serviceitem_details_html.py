from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0042_order_requires_boss_selection_orderapplication')]
    operations = [
        migrations.AddField('gameplay', 'details_html', models.TextField(blank=True, default='', help_text='价目下方的玩法富文本；商品可单独覆盖')),
        migrations.AddField('serviceitem', 'details_html', models.TextField(blank=True, default='', help_text='当前价目玩法详情；留空使用所属玩法的富文本')),
    ]
