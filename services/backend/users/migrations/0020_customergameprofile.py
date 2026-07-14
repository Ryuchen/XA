import django.db.models.deletion
from django.db import migrations, models


def backfill_legacy_game_profiles(apps, schema_editor):
    User = apps.get_model('users', 'CustomUser')
    CustomerGameProfile = apps.get_model('users', 'CustomerGameProfile')
    GameCategory = apps.get_model('orders', 'GameCategory')
    Order = apps.get_model('orders', 'Order')
    fallback_category_id = GameCategory.objects.filter(is_active=True).order_by('sort_order', 'id').values_list('id', flat=True).first()
    if not fallback_category_id:
        return
    for user in User.objects.filter(role='CUSTOMER').iterator():
        if not (user.game_region or user.game_nickname or user.game_uid):
            continue
        category_id = (
            Order.objects.filter(customer_id=user.id, service__game_category__isnull=False)
            .order_by('-created_at')
            .values_list('service__game_category_id', flat=True)
            .first()
        ) or fallback_category_id
        CustomerGameProfile.objects.get_or_create(
            user_id=user.id,
            game_category_id=category_id,
            defaults={
                'region': user.game_region,
                'nickname': user.game_nickname,
                'uid': user.game_uid,
                'is_default': True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0015_order_support_contact'),
        ('users', '0019_correct_checkin_currency_thresholds'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerGameProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('region', models.CharField(blank=True, default='', max_length=50)),
                ('nickname', models.CharField(blank=True, default='', max_length=50)),
                ('uid', models.CharField(blank=True, default='', max_length=50)),
                ('is_default', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('game_category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='customer_profiles', to='orders.gamecategory')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_profiles', to='users.customuser')),
            ],
            options={
                'ordering': ['game_category__sort_order', 'game_category_id'],
            },
        ),
        migrations.AddConstraint(
            model_name='customergameprofile',
            constraint=models.UniqueConstraint(fields=('user', 'game_category'), name='uniq_customer_game_profile'),
        ),
        migrations.RunPython(backfill_legacy_game_profiles, migrations.RunPython.noop),
    ]
