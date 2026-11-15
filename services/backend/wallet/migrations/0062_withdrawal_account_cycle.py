from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0061_deposit_refund_application')]
    operations = [
        migrations.RemoveConstraint(model_name='withdrawalaccount', name='unique_withdraw_account_method'),
        migrations.AddField(model_name='withdrawalaccount', name='business_cycle',
                            field=models.PositiveIntegerField(editable=False, null=True)),
        migrations.AlterField(model_name='withdrawalaccount', name='business_cycle',
                              field=models.PositiveIntegerField(editable=False)),
        migrations.AddConstraint(model_name='withdrawalaccount', constraint=models.UniqueConstraint(
            fields=('account', 'business_cycle', 'payee_method'), name='withdraw_cycle_method_unique')),
    ]
