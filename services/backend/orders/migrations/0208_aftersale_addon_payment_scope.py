import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0207_grab_actor_membership_cycle')]

    operations = [
        migrations.AddField(
            model_name='aftersale', name='addon_payment',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonpayment'),
        ),
    ]
