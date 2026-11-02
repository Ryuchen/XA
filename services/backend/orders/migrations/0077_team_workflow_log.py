from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0076_remove_orderapplication_uniq_order_application_and_more')]

    operations = [migrations.AlterField(
        model_name='orderstatuslog', name='action',
        field=models.CharField(choices=[('CREATE', '创建'), ('GRAB', '接单'), ('ASSIGN', '派单'),
            ('START', '开始服务'), ('COMPLETE', '完成'), ('REJECT', '拒单'), ('CANCEL', '取消'),
            ('REFUND', '退款'), ('TRANSFER', '转单'), ('AUTO_CANCEL', '超时自动取消'),
            ('TEAM_WORKFLOW', '启用双人凭证流程')], db_index=True, max_length=20),
    )]
