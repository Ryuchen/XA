from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('engagement', '0041_license_gift_vip_review'),
                    ('users', '0055_customer_game_profile_account_owner')]
    operations = [
        migrations.CreateModel(name='AchievementLicenseEvent', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('event_key', models.CharField(max_length=100, unique=True)),
            ('business_cycle', models.PositiveIntegerField()), ('outcome', models.CharField(max_length=8)),
            ('license_evidence', models.JSONField(default=dict)), ('metrics', models.JSONField(default=dict)),
            ('eligible', models.JSONField(default=list)), ('decided_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='orders.order')),
        ]),
        migrations.CreateModel(name='EarnedAchievement', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('business_cycle', models.PositiveIntegerField()), ('earned_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
            ('achievement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='users.achievement')),
            ('source_event', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='engagement.achievementlicenseevent')),
        ], options={'constraints': [models.UniqueConstraint(fields=('account', 'business_cycle', 'achievement'),
                                                             name='unique_earned_achievement_cycle')]}),
    ]
