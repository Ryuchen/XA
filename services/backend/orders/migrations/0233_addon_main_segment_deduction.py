from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0232_aftersale_segment_scope')]
    operations = [migrations.CreateModel(name='OrderAddonRefundMainSegmentDeduction', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('amount', models.PositiveBigIntegerField()),
        ('participant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='main_segment_deductions', to='orders.orderaddonrefundparticipant')),
        ('segment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='addon_main_deductions', to='orders.orderparticipationsegment')),
    ], options={'constraints': [
        models.UniqueConstraint(fields=('participant', 'segment'), name='addon_main_segment_unique'),
        models.CheckConstraint(condition=models.Q(amount__gt=0), name='addon_main_segment_positive'),
    ]})]
