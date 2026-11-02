from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0073_evaluation_reply_version')]

    operations = [
        migrations.RemoveConstraint(model_name='directrefundinbox', name='direct_refund_inbox_status'),
        migrations.AlterField(model_name='directrefundinbox', name='status',
            field=models.CharField(choices=[('PENDING', '等待渠道查询'), ('DONE', '渠道状态已核对'),
                                            ('REVIEW', '待人工核对')], db_index=True, default='PENDING', max_length=8)),
        migrations.AddConstraint(model_name='directrefundinbox',
            constraint=models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'DONE', 'REVIEW']),
                                              name='direct_refund_inbox_status')),
    ]
