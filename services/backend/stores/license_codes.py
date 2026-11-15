"""License-code lifecycle shared by platform and tenant endpoints.

Activation must only be called after tenant authentication and explicit store
authorization. The caller supplies the verified store and account, never input
from a request body. All control-plane changes and their audit are atomic.
"""
import hashlib
import secrets
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from .license_policy import TIERS
from .models import PlatformAuditEvent, Store, StoreLicenseActivationAudit, StoreLicenseCode, StoreLicenseContract
from .platform_accounts import _locked_actor


class LicenseCodeError(ValueError):
    pass


def _digest(code):
    return hashlib.sha256(code.encode('ascii')).hexdigest()


def _one_year_after(value):
    """Calendar year in the contract's Beijing timezone, including leap day."""
    try:
        local = value.astimezone(ZoneInfo('Asia/Shanghai'))
    except (OverflowError, ValueError):
        raise LicenseCodeError('授权结束时间超出范围') from None
    try:
        return local.replace(year=local.year + 1)
    except (OverflowError, ValueError):
        if local.month == 2 and local.day == 29 and local.year < 9999:
            return local.replace(year=local.year + 1, day=28)
        raise LicenseCodeError('授权结束时间超出范围') from None


def issue(token, *, store_slug, tier, kind, starts_at=None, ends_at=None, backdate_reason='', amount_due_fen=None):
    if not isinstance(tier, str) or tier not in TIERS or not isinstance(kind, str) or kind not in {'INITIAL', 'RENEW', 'CHANGE'}:
        raise LicenseCodeError('授权档位或类型无效')
    if not isinstance(backdate_reason, str) or len(backdate_reason.strip()) > 200:
        raise LicenseCodeError('生效原因无效')
    if amount_due_fen is None:
        amount_due_fen = TIERS[tier].annual_price_fen
    if type(amount_due_fen) is not int or not 0 <= amount_due_fen <= 2 ** 63 - 1:
        raise LicenseCodeError('合同应收金额无效')
    if any(value is not None and (not hasattr(value, 'tzinfo') or timezone.is_naive(value))
           for value in (starts_at, ends_at)):
        raise LicenseCodeError('授权起止时间无效')
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        store = Store.objects.select_for_update().filter(slug=store_slug).first()
        if not store:
            raise LicenseCodeError('门店不存在')
        now = timezone.now()
        # Serialize issuance per store. Issued and activated future arrangements
        # both occupy the one pending slot until voided or effective.
        pending = StoreLicenseCode.objects.filter(store=store, voided_at__isnull=True, ends_at__gt=now).filter(
            Q(starts_at__gt=now) | Q(activated_at__isnull=True))
        if pending.exists():
            raise LicenseCodeError('请先取消现有待生效或待激活授权')
        current = (StoreLicenseCode.objects.filter(store=store, activated_at__isnull=False, voided_at__isnull=True)
                   .annotate(effective_at=Greatest(F('starts_at'), F('activated_at')))
                   .filter(effective_at__lte=now).order_by('-effective_at', '-activated_at', '-id').first())
        if kind == 'INITIAL' and current:
            raise LicenseCodeError('门店已有授权，须使用续期或调档')
        if kind in {'RENEW', 'CHANGE'} and not current:
            raise LicenseCodeError('门店尚无已激活授权')
        if starts_at is None:
            starts_at = current.ends_at if kind == 'RENEW' else now
        if ends_at is None:
            ends_at = current.ends_at if kind == 'CHANGE' else _one_year_after(starts_at)
        if ends_at <= starts_at or ends_at <= now:
            raise LicenseCodeError('授权起止时间无效')
        if starts_at < now and not backdate_reason.strip():
            raise LicenseCodeError('过去生效须填写原因')
        if kind == 'CHANGE' and ends_at != current.ends_at:
            raise LicenseCodeError('调档不改变当前授权到期时间')
        if kind == 'RENEW' and (tier != current.tier or ends_at <= current.ends_at):
            raise LicenseCodeError('续期须延长当前到期时间，调档请单独操作')
        code = secrets.token_urlsafe(32)
        contract = StoreLicenseContract.objects.create(store=store, amount_due_fen=amount_due_fen,
                                                       created_by=actor, updated_by=actor)
        row = StoreLicenseCode.objects.create(store=store, contract=contract, code_digest=_digest(code), tier=tier, kind=kind,
            starts_at=starts_at, ends_at=ends_at, backdate_reason=backdate_reason.strip(), created_by=actor)
        PlatformAuditEvent.objects.create(account=actor, action='license.code_issued', target_store_slug=store.slug,
            target_type='license_code', target_id=str(row.pk), detail={
                'tier': tier, 'kind': kind, 'starts_at': starts_at.isoformat(), 'ends_at': ends_at.isoformat(),
                'backdate_reason': backdate_reason.strip(), 'contract_id': str(contract.pk), 'amount_due_fen': amount_due_fen})
        return row, code


def reissue(token, *, code_id):
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        store_id = StoreLicenseCode.objects.filter(pk=code_id).values_list('store_id', flat=True).first()
        if store_id is None:
            raise LicenseCodeError('授权记录不存在')
        # Activation and issuance lock the store first. Use the same order so
        # simultaneous activation and reissue cannot wait on one another.
        store = Store.objects.select_for_update().get(pk=store_id)
        old = StoreLicenseCode.objects.select_for_update().get(pk=code_id, store=store)
        now = timezone.now()
        if not old.voided_at or old.ends_at <= now:
            raise LicenseCodeError('仅已作废且仍在授权期内的码可重新签发')
        if StoreLicenseCode.objects.select_for_update().filter(reissued_from=old).exists():
            raise LicenseCodeError('该授权码已重新签发，请核对最新记录')
        pending = StoreLicenseCode.objects.filter(store=store, voided_at__isnull=True, ends_at__gt=now).filter(
            Q(starts_at__gt=now) | Q(activated_at__isnull=True))
        if pending.select_for_update().exists():
            raise LicenseCodeError('请先取消现有待生效或待激活授权')
        # A voided code can outlive changes to the store's current license.
        # Reissuing its old terms must not bypass the same lifecycle rules as
        # a new issue, or a stale INITIAL/CHANGE/RENEW can supersede them.
        current = (StoreLicenseCode.objects.filter(store=store, activated_at__isnull=False, voided_at__isnull=True)
                   .annotate(effective_at=Greatest(F('starts_at'), F('activated_at')))
                   .filter(effective_at__lte=now).order_by('-effective_at', '-activated_at', '-id').first())
        if old.kind == 'INITIAL' and current:
            raise LicenseCodeError('门店已有授权，不能重发首次授权码')
        if old.kind in {'RENEW', 'CHANGE'} and not current:
            raise LicenseCodeError('门店尚无已激活授权，不能重发该授权码')
        if old.kind == 'CHANGE' and old.ends_at != current.ends_at:
            raise LicenseCodeError('当前授权到期时间已变化，不能重发原调档码')
        if old.kind == 'RENEW' and (old.tier != current.tier or old.ends_at <= current.ends_at):
            raise LicenseCodeError('当前授权条款已变化，不能重发原续期码')
        code = secrets.token_urlsafe(32)
        row = StoreLicenseCode.objects.create(store=store, contract=old.contract, reissued_from=old,
            code_digest=_digest(code), tier=old.tier, kind=old.kind, starts_at=old.starts_at, ends_at=old.ends_at,
            backdate_reason=old.backdate_reason, created_by=actor)
        PlatformAuditEvent.objects.create(account=actor, action='license.code_reissued', target_store_slug=store.slug,
            target_type='license_code', target_id=str(row.pk), detail={'previous_id': str(old.pk),
                                                                        'contract_id': str(old.contract_id)})
        return row, code


def void(token, *, code_id, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
        raise LicenseCodeError('请填写作废原因')
    with transaction.atomic(using='default'):
        actor = _locked_actor(token)
        store_id = StoreLicenseCode.objects.filter(pk=code_id).values_list('store_id', flat=True).first()
        if store_id is None:
            raise LicenseCodeError('授权记录不存在')
        # Activation locks the store before the code; cancellation must not
        # take those rows in the opposite order.
        store = Store.objects.select_for_update().get(pk=store_id)
        row = StoreLicenseCode.objects.select_for_update().get(pk=code_id, store=store)
        if row.voided_at:
            return row
        if row.activated_at and row.starts_at <= timezone.now():
            raise LicenseCodeError('已生效的授权不能通过作废码取消')
        if row.activated_at:
            # The local cache must not retain a scheduled grant after the
            # cancellation commits, including during a control-plane outage.
            from .license_snapshot import invalidate_for_cancellation
            invalidate_for_cancellation(store)
            # The tenant write can block until after the scheduled start.
            # Do not cancel an authorization that has become effective.
            if row.starts_at <= timezone.now():
                raise LicenseCodeError('授权已生效，不能通过作废码取消')
        row.voided_at, row.voided_by, row.void_reason = timezone.now(), actor, reason.strip()
        row.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
        PlatformAuditEvent.objects.create(account=actor, action='license.code_voided',
            target_store_slug=store.slug, target_type='license_code', target_id=str(row.pk),
            detail={'reason': reason.strip(), 'activated': bool(row.activated_at)})
        if row.activated_at:
            from .license_notices import enqueue_cancellation
            enqueue_cancellation(row)
            # Invalidation is already durable. Refresh is best effort after
            # the control commit and cannot turn a committed cancel into an
            # apparent failure; later business reads also refresh the cache.
            from .license_snapshot import _persist_after_commit
            transaction.on_commit(lambda: _persist_after_commit(store, None, None), using='default', robust=True)
        return row


def _activate_after_verified_store_auth(*, store, actor_id, code, authorize=None):
    """Private integration boundary, not a substitute for authenticating staff."""
    if (not isinstance(store, Store) or not isinstance(actor_id, int) or isinstance(actor_id, bool)
            or actor_id <= 0 or not isinstance(code, str) or not code.isascii() or len(code) > 100):
        raise LicenseCodeError('授权码无效')
    with transaction.atomic(using='default'):
        # Same global anchor as platform changes prevents authorization races.
        from .database_identity import lock_store_registration
        lock_store_registration()
        locked_store = Store.objects.select_for_update().filter(pk=store.pk, slug=store.slug).first()
        if not locked_store:
            raise LicenseCodeError('授权码无效')
        if authorize is not None:
            authorize(locked_store)
        row = StoreLicenseCode.objects.select_for_update().filter(code_digest=_digest(code), store=locked_store).first()
        if not row:
            raise LicenseCodeError('授权码无效或已作废')
        if row.activated_at:
            # Return the original result (including cancellation state), never
            # turn a cancelled future arrangement back on.
            return row, False
        if row.voided_at:
            raise LicenseCodeError('授权码无效或已作废')
        now = timezone.now()
        if now >= row.ends_at:
            raise LicenseCodeError('授权已结束，请联系平台重新签发')
        # A previously verified tenant cache may not contain this newly
        # activated grant. Remove it before the control-plane commit so that
        # a subsequent control-plane outage cannot revive the old schedule.
        from .license_snapshot import prepare_policy_cache_refresh, refresh_policy_caches
        refresh_stores = prepare_policy_cache_refresh([locked_store])
        row.activated_at, row.activated_by_id = now, actor_id
        row.save(update_fields=['activated_at', 'activated_by_id'])
        StoreLicenseActivationAudit.objects.create(license_code=row, store_slug=locked_store.slug, actor_id=actor_id)
        from .license_notices import collect_store_notice
        collect_store_notice(locked_store, now=now)
        if refresh_stores:
            transaction.on_commit(lambda: refresh_policy_caches(refresh_stores), using='default', robust=True)
        return row, True
