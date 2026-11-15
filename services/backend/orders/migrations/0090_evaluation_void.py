import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0089_evaluation_low_rating_aftersale')]

    operations = [
        migrations.AddField(model_name='evaluation', name='voided_at',
                            field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='evaluation', name='void_reason',
                            field=models.CharField(blank=True, default='', editable=False, max_length=1000)),
        migrations.AddField(model_name='evaluation', name='voided_by',
                            field=models.ForeignKey(blank=True, editable=False, null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name='voided_evaluations', to='club_accounts.clubaccount')),
    ]
