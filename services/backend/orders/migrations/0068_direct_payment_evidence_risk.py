from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0067_direct_payment_test_query')]

    operations = [
        migrations.AddField(
            model_name='directpaymentintent',
            name='evidence_risk_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Downgrade must not erase a pending stop-dispatch decision.
        migrations.RunPython(migrations.RunPython.noop),
    ]
