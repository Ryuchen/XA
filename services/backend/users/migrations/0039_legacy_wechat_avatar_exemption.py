from django.db import migrations
from django.db.models import Exists, OuterRef, Q


def preserve_verified_legacy_providers(apps, schema_editor):
    """Exempt only already-bound providers with an existing accepted service.

    This records a legacy exemption, never a fabricated WeChat-avatar proof.
    Each tenant migration reads and writes only its own database alias.
    """
    alias = schema_editor.connection.alias
    Profile = apps.get_model('users', 'EscortProfile')
    Identity = apps.get_model('club_accounts', 'WechatIdentity')
    Order = apps.get_model('orders', 'Order')
    Participant = apps.get_model('orders', 'OrderProvider')
    accepted = Q(status__in=['GRABBED', 'IN_SERVICE', 'COMPLETED']) | Q(grabbed_at__isnull=False)
    direct = Order.objects.using(alias).filter(is_gift_snapshot=False).filter(accepted).filter(
        Q(provider_account_id=OuterRef('account_id')) | Q(provider_id=OuterRef('user_id')))
    participant = Participant.objects.using(alias).filter(order__is_gift_snapshot=False).filter(
        Q(order__status__in=['GRABBED', 'IN_SERVICE', 'COMPLETED']) | Q(order__grabbed_at__isnull=False)
    ).filter(Q(provider_account_id=OuterRef('account_id')) | Q(provider_id=OuterRef('user_id')))
    identity = Identity.objects.using(alias).filter(account_id=OuterRef('account_id'))
    (Profile.objects.using(alias).filter(account__is_openid_bound=True)
        .annotate(_identity=Exists(identity), _direct=Exists(direct), _participant=Exists(participant))
        .filter((Q(account__openid__isnull=False) & ~Q(account__openid='')) | Q(_identity=True))
        .filter(Q(_direct=True) | Q(_participant=True))
        .update(wechat_avatar_legacy_exempt=True))


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0038_escortprofile_wechat_avatar_legacy_exempt_and_more'),
        ('orders', '0085_subsidy_share_retained_amount'),
        ('club_accounts', '0006_wechat_identity'),
    ]
    operations = [migrations.RunPython(preserve_verified_legacy_providers, migrations.RunPython.noop)]
