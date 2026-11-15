from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0211_order_responsible_staff'),
        ('club_accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderProviderCompletionFact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('evidence', models.CharField(max_length=500)),
                ('completed_at', models.DateTimeField()),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='provider_completion_facts', to='orders.order')),
                ('provider_account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('segment', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='completion_fact', to='orders.orderparticipationsegment')),
            ],
            options={'constraints': [models.UniqueConstraint(condition=models.Q(segment__isnull=True), fields=('order', 'provider_account'), name='unique_unsegmented_team_completion')]},
        ),
    ]
