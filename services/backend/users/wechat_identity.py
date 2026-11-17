"""Provider cross-channel identity checks using only WeChat-supplied facts."""
from club_accounts.models import WechatIdentity
from django.db import IntegrityError
from stores.db import transaction
from .web_login_models import ProviderWechatUnionOwner


H5_CHANNEL = 'OFFICIAL_H5'


def claim_provider_unionid(account, unionid):
    if not unionid:
        return
    try:
        with transaction.atomic():
            owner = ProviderWechatUnionOwner.objects.create(unionid=unionid, account=account)
    except IntegrityError:
        owner = ProviderWechatUnionOwner.objects.select_for_update().get(unionid=unionid)
    if owner.account_id != account.pk:
        raise ValueError('当前微信已绑定其他账号，请联系客服')


def clean_unionid(value):
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or len(value) > 100 or not value.strip():
        raise ValueError('微信身份信息无效，请重新授权')
    return value


def require_matching_unionids(first, second):
    if not first or not second:
        raise ValueError('暂无法核验两个微信渠道是否属于本人，请联系客服')
    if first != second:
        raise ValueError('当前微信与已绑定微信不一致，请联系客服')


def check_h5_provider_binding(account, unionid):
    """Caller holds the provider account lock."""
    if not account.openid:
        return
    h5 = WechatIdentity.objects.select_for_update().filter(account=account, channel=H5_CHANNEL,
        appid=account.wechat_appid, openid=account.openid).first()
    require_matching_unionids(h5.unionid if h5 else '', unionid)


def bind_provider(account, *, appid, openid, unionid):
    """Persist official-account H5 proof after account lock and cross-channel validation."""
    unionid = clean_unionid(unionid)
    own = WechatIdentity.objects.select_for_update().filter(account=account, channel=H5_CHANNEL, appid=appid)
    if own.exclude(openid=openid).exists():
        raise ValueError('该账号已绑定其他微信，请联系客服')
    identity = WechatIdentity.objects.select_for_update().filter(channel=H5_CHANNEL, appid=appid, openid=openid).first()
    if identity and identity.account_id != account.pk:
        raise ValueError('当前微信已绑定其他账号，请联系客服')
    if identity and identity.unionid and unionid and identity.unionid != unionid:
        raise ValueError('微信身份信息不一致，请联系客服')
    effective_unionid = unionid or (identity.unionid if identity else '')
    claim_provider_unionid(account, effective_unionid)
    if identity is None:
        WechatIdentity.objects.create(account=account, channel=H5_CHANNEL, appid=appid,
                                      openid=openid, unionid=unionid)
    elif unionid and not identity.unionid:
        identity.unionid = unionid
        identity.save(update_fields=['unionid', 'updated_at'])
