from django.conf import settings
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from .models import Wallet


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    """新建 CustomUser 时同步开钱包，并尽力补上 ClubAccount 维度。

    这里是历史上「account=NULL 钱包」的主要来源：用户先建、ClubAccount 后建，
    钱包只挂在 user 维度上。后续 account 维度访问查不到就会走创建分支撞唯一
    约束。现在建号时就查一次映射表把两个维度写全；映射尚未建立的场景由
    ``wallet.services.get_wallet`` 在首次访问时自愈回填。
    """
    if not created:
        return
    from club_accounts.models import LegacyAccountMap

    mapping = LegacyAccountMap.objects.filter(
        legacy_user_id=instance.pk,
    ).only('account_id').first()
    Wallet.objects.get_or_create(
        user=instance,
        defaults={'account_id': mapping.account_id if mapping else None},
    )


@receiver(post_migrate)
def ensure_platform_wallet(sender, **kwargs):
    if getattr(sender, 'name', '') != 'wallet':
        return
    if getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and kwargs.get('using') == 'default':
        return
    from .models import get_platform_wallet
    from stores.context import current_store, store_context
    alias = kwargs.get('using') or 'default'
    store = current_store()
    if store is None or store.database_alias != alias:
        # post_migrate/flush may run without the migrate command's store
        # context; never write a tenant's bookkeeping wallet to another DB.
        from types import SimpleNamespace
        store = SimpleNamespace(database_alias=alias,
                                slug='shenglang' if alias == 'default' else alias.replace('_', '-'))
    with store_context(store):
        get_platform_wallet()
