"""Experimental, account-serialized permit for one refund-gap recovery order."""
from dataclasses import dataclass

from django.conf import settings
from django.db.models import BigIntegerField, Exists, OuterRef, Q, Subquery, Sum, Value
from django.utils import timezone
from stores.db import transaction

from .models import RefundDepositDeficit, RefundDepositRecoveryPermit
from .refund_deposit import enabled as deficit_enabled


@dataclass(frozen=True)
class RecoveryOfferRead:
    has_account_deficit: bool
    remaining: int | None
    has_open_permit: bool

    @classmethod
    def from_roster(cls, profile):
        return cls(profile.has_account_refund_deficit, profile.current_refund_deficit, profile.has_open_recovery_permit)


def annotate_recovery_offer(profiles, *, allow_recovery):
    """Read same-store eligibility facts with the roster, including legacy account gaps."""
    deficits = RefundDepositDeficit.objects.filter(account_id=OuterRef('account_id'), remaining__gt=0)
    remaining = Value(None, output_field=BigIntegerField())
    open_permit = Value(False)
    if allow_recovery:
        own_deficits = deficits.filter(profile_id=OuterRef('pk')).order_by().values('profile_id')
        remaining = Subquery(own_deficits.annotate(total=Sum('remaining')).values('total'))
        open_permit = Exists(RefundDepositRecoveryPermit.objects.filter(
            account_id=OuterRef('account_id'), closed_at__isnull=True,
        ))
    return profiles.annotate(
        has_account_refund_deficit=Exists(deficits),
        current_refund_deficit=remaining,
        has_open_recovery_permit=open_permit,
    )


def enabled():
    return deficit_enabled() and bool(getattr(settings, 'REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED', False))


def tracking_enabled():
    # Keep existing permits safe after disabling new assignment.
    return enabled() or bool(getattr(settings, 'REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED', False))


def _atomic():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('补差接单机会必须在门店事务内处理')


def _remaining(profile):
    return RefundDepositDeficit.objects.filter(profile=profile, account_id=profile.account_id).aggregate(
        value=Sum('remaining'))['value'] or 0


def eligibility_error(profile, *, existing_order_id=None, lock_current=False, offer_read=None,
                      deposit_required_override=None):
    """Use current locked facts only when the caller already holds the profile lock."""
    if not enabled() or not profile.account_id:
        return '退款押金缺口待补，补差接单功能尚未开放'
    use_offer_read = offer_read is not None and not lock_current and existing_order_id is None
    if use_offer_read:
        remaining = offer_read.remaining
    elif lock_current:
        _atomic()
        deficits = list(RefundDepositDeficit.objects.select_for_update().filter(
            account_id=profile.account_id, remaining__gt=0).order_by('created_at', 'pk'))
        if any(row.profile_id != profile.pk for row in deficits):
            raise ValueError('退款押金缺口与陪玩资料不匹配')
        remaining = sum(row.remaining for row in deficits)
    else:
        remaining = _remaining(profile)
    if not remaining:
        return '退款押金缺口已变化，请刷新后重试'
    if use_offer_read:
        blocked_by_permit = offer_read.has_open_permit
    else:
        permits = RefundDepositRecoveryPermit.objects.filter(
            account_id=profile.account_id, closed_at__isnull=True,
        ).order_by('pk')
        permits = list(permits.select_for_update() if lock_current else permits)
        blocked_by_permit = permits and not (
            len(permits) == 1 and permits[0].profile_id == profile.pk
            and existing_order_id is not None and permits[0].order_id == existing_order_id
        )
    if blocked_by_permit:
        return '补差订单尚未完成结算，暂不能再接新单'
    if profile.deposit_mode != 'EARNINGS':
        required = (deposit_required_override if use_offer_read and deposit_required_override is not None
                    else profile.effective_deposit_required)
        if max(required - profile.deposit_paid, 0) > remaining:
            return '陪玩存在非退款造成的押金不足，请先补缴'
    return ''


class RecoveryIneligible(ValueError):
    """Current, locked facts prove this provider cannot take another recovery order."""


def reserve(profile, order, *, allow_in_service=False):
    """Caller holds the order and provider dispatch lock in the assignment transaction."""
    _atomic()
    if not enabled():
        raise ValueError('补差接单功能未启用')
    from users.models import EscortProfile
    profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
    # Locking reads see the latest committed facts on MySQL REPEATABLE READ.
    # The earlier eligibility hint may belong to an older transaction snapshot.
    deficits = list(RefundDepositDeficit.objects.select_for_update().filter(
        account_id=profile.account_id, remaining__gt=0).order_by('created_at', 'pk'))
    if any(row.profile_id != profile.pk for row in deficits):
        raise ValueError('退款押金缺口与陪玩资料不匹配')
    remaining = sum(row.remaining for row in deficits)
    if not remaining:
        return None
    existing = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        account_id=profile.account_id, closed_at__isnull=True).order_by('pk'))
    if existing and not (len(existing) == 1 and existing[0].profile_id == profile.pk
                         and existing[0].order_id == order.pk):
        raise RecoveryIneligible('补差订单尚未完成结算，暂不能再接新单')
    if (profile.deposit_mode != 'EARNINGS'
            and max(profile.effective_deposit_required - profile.deposit_paid, 0) > remaining):
        raise RecoveryIneligible('陪玩存在非退款造成的押金不足，请先补缴')
    primary = order.provider_account_id == profile.account_id and order.provider_id == profile.user_id
    assigned = order.providers.filter(provider_account_id=profile.account_id, provider_id=profile.user_id,
                                      released_at__isnull=True).exists()
    valid_status = order.status == order.Status.GRABBED
    if allow_in_service and order.status == order.Status.IN_SERVICE:
        from orders.models import OrderParticipationSegment, OrderSegmentFinancialCoverage, OrderPlatformSubsidy
        from orders.direct_payment_models import DirectPaymentIntent
        from orders.segment_financial_capture import TRANSFER_RATE_REVIEW
        from orders.segment_settlement import enabled as segment_settlement_enabled
        valid_status = bool(segment_settlement_enabled() and order.in_service_at and not order.settled_at
            and order.settlement_mode == order.SettlementMode.REPORT_APPROVAL
            and OrderSegmentFinancialCoverage.objects.filter(attribution__order=order,
                attribution__status='ACTIVE', status='REVIEW', review_reason=TRANSFER_RATE_REVIEW,
                source='WALLET_NO_POINTS', deal_amount=order.amount).exists()
            and not order.refund_records.exists() and not OrderPlatformSubsidy.objects.filter(order=order).exists()
            and not DirectPaymentIntent.objects.filter(order=order).exists()
            and order.service_clock_version is not None and order.service_freeze_hours is not None
            and order.service_aftersale_hours is not None
            and OrderParticipationSegment.objects.filter(attribution__order=order,
                provider_account_id=profile.account_id, released_at__isnull=True,
                financial_snapshot__isnull=False, original_transfer_in__isnull=False).count() == 1)
    if (not (primary or assigned) or not valid_status
            or order.payment_status != order.PaymentStatus.PAID or order.is_gift_snapshot):
        raise ValueError('补差订单与陪玩不匹配')
    if len(existing) == 1 and existing[0].profile_id == profile.pk and existing[0].order_id == order.pk:
        return existing[0]
    return RefundDepositRecoveryPermit.objects.create(profile=profile, account_id=profile.account_id, order=order)


def reserve_many(profiles, order, *, allow_in_service=False):
    """Reserve team members in the same stable order as their dispatch locks."""
    _atomic()
    profiles = list(profiles)
    if not all(profile.account_id for profile in profiles) or len({p.account_id for p in profiles}) != len(profiles):
        raise ValueError('补差订单陪玩身份不完整或重复')
    profiles.sort(key=lambda profile: profile.account_id)
    if allow_in_service:
        return [reserve(profile, order, allow_in_service=True) for profile in profiles]
    return [reserve(profile, order) for profile in profiles]


def close_for_income(profile, application):
    """Called under profile lock after the real income and paired repayments post."""
    _atomic()
    if not tracking_enabled():
        return
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits:
        return
    permit = permits[0]
    no_gap = not _remaining(profile)
    if not no_gap and permit.order_id != application.source_transaction.order_id:
        return
    permit.closed_at = timezone.now()
    permit.close_reason = 'GAP_PAID' if no_gap else 'INCOME_SETTLED'
    permit.income_application = application
    permit.save(update_fields=['closed_at', 'close_reason', 'income_application'])


def close_for_zero_settlement(profile, settlement):
    """A verified zero-income person settlement still consumes the one order."""
    _atomic()
    if not tracking_enabled():
        return
    if (settlement.account_id != profile.account_id or settlement.amount != 0
            or settlement.income_transaction_id or settlement.manifest.order.status != 'COMPLETED'):
        raise ValueError('零收益结算凭证与陪玩不匹配')
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != settlement.manifest.order_id:
        return
    permit = permits[0]
    permit.closed_at = timezone.now(); permit.close_reason = 'ZERO_INCOME_SETTLED'
    permit.zero_income_settlement = settlement
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_income_settlement'])


def close_for_zero_service(profile, assignment):
    """Consume the opportunity only after the person's real service settlement."""
    _atomic()
    if not tracking_enabled():
        return
    from orders.models import OrderAddonPayment
    from .models import Transaction
    order = assignment.order
    if not profile:
        if RefundDepositRecoveryPermit.objects.filter(account_id=assignment.provider_account_id,
                order=order, closed_at__isnull=True).exists():
            raise ValueError('补差机会缺少陪玩档案')
        return
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != order.pk:
        return
    # Add-on income is a separate obligation; its own settlement must close
    # the permit, so a zero main-order share alone cannot do so.
    if OrderAddonPayment.objects.filter(intent__order=order).exists():
        return
    if (assignment.provider_account_id != profile.account_id
            or assignment.provider_id != profile.user_id or not assignment.settled_at
            or assignment.provider_income != 0 or order.status != order.Status.COMPLETED
            or order.payment_status != order.PaymentStatus.PAID or order.refund_records.exists()
            or Transaction.objects.filter(order=order, wallet__account_id=profile.account_id,
                tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                remark=f'订单收入：{order.order_no}').exists()):
        raise ValueError('普通订单零收益结算凭证与陪玩不匹配')
    permit = permits[0]
    permit.closed_at = timezone.now()
    permit.close_reason = 'ZERO_SERVICE_SETTLED'
    permit.zero_service_assignment = assignment
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_service_assignment'])


def close_for_zero_initial_points(profile, person, addon_parts):
    """Close only after the funded points order's own zero earnings settle."""
    _atomic()
    if not tracking_enabled():
        return
    order = person.settlement.order
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != order.pk:
        return
    parts = [part for part in addon_parts if part.provider_account_id == profile.account_id]
    if any(part.provider_income_units for part in parts):
        return
    expected = {(part.payment_id, part.segment_id) for part in parts}
    rows = list(person.settlement.addon_settlements.filter(segment__provider_account_id=profile.account_id))
    if (not person.settlement.redemption_id or person.amount or person.income_transaction_id
            or order.status != order.Status.COMPLETED or order.payment_status != order.PaymentStatus.PAID
            or order.refund_records.exists() or person.segment.attribution.order_id != order.pk
            or person.segment.provider_account_id != profile.account_id or not person.settled_at
            or person.report.order_id != order.pk or person.report.provider_account_id != profile.account_id
            or len(expected) != len(parts) or {(row.payment_id, row.segment_id) for row in rows} != expected
            or len(rows) != len(expected) or any(row.amount or row.income_transaction_id
                or row.settled_at != person.settled_at for row in rows)):
        raise ValueError('积分订单本人零收益或加购结算凭证不匹配')
    permit = permits[0]
    permit.closed_at = timezone.now()
    permit.close_reason = 'ZERO_INITIAL_PTS_SETTLED'
    permit.zero_initial_points_person = person
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_initial_points_person'])


def close_for_zero_unaffected(profile, assignment, execution, report):
    """Close a recovery permit from the actual unaffected-person settlement."""
    _atomic()
    if not tracking_enabled():
        return
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != assignment.order_id:
        return
    order = assignment.order
    from orders.models import OrderAddonPayment
    if OrderAddonPayment.objects.filter(intent__order=order).exists():
        return
    evidence = execution.unaffected_report_evidence
    item = evidence.get(str(assignment.pk)) if isinstance(evidence, dict) else None
    execution_order_id = execution.zero_order_id if execution.zero_order_id else execution.refund.order_id
    if (order.status != order.Status.COMPLETED or execution_order_id != order.pk
            or assignment.provider_account_id != profile.account_id or assignment.provider_id != profile.user_id
            or assignment.provider_income != 0 or not assignment.settled_at
            or execution.shares.filter(account_id=profile.account_id).exists()
            or not isinstance(item, dict) or item.get('report_id') != report.pk
            or item.get('submitted_at') != report.submitted_at.isoformat()
            or item.get('settled_at') != assignment.settled_at.isoformat()
            or item.get('income_transaction_id') is not None
            or report.order_id != order.pk or report.provider_account_id != profile.account_id
            or report.status != report.Status.APPROVED):
        raise ValueError('未受影响陪玩的零收益结算凭证不匹配')
    permit = permits[0]
    permit.closed_at = timezone.now()
    permit.close_reason = 'ZERO_UNAFFECTED_SETTLED'
    permit.zero_unaffected_assignment, permit.zero_unaffected_execution = assignment, execution
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_unaffected_assignment', 'zero_unaffected_execution'])


def close_for_zero_negotiated(profile, share):
    """A completed negotiated recovery order can consume a zero-income permit."""
    _atomic()
    if not tracking_enabled():
        return
    execution = share.execution
    order = execution.zero_order if execution.zero_order_id else execution.refund.order
    if (share.account_id != profile.account_id or share.assignment.provider_id != profile.user_id
            or share.assignment.order_id != order.pk or share.retained_income != 0
            or not share.settled_at or share.income_transaction_id or order.status != 'COMPLETED'):
        raise ValueError('协商零收益结算凭证与陪玩不匹配')
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != order.pk:
        return
    permit = permits[0]
    permit.closed_at = timezone.now(); permit.close_reason = 'ZERO_NEGOTIATED_SETTLED'
    permit.zero_negotiated_share = share
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_negotiated_share'])


def close_for_zero_points_refund(profile, person):
    """A matured zero-income points refund also consumes its recovery order."""
    _atomic()
    if not tracking_enabled():
        return
    order = person.settlement.posting.points_refund.redemption.order
    assignment = order.providers.filter(provider_account_id=profile.account_id, provider_id=profile.user_id).first()
    if (person.amount != 0 or person.income_transaction_id or order.status != 'COMPLETED'
            or person.segment.attribution.order_id != order.pk or person.segment.provider_account_id != profile.account_id
            or not assignment or assignment.settled_at != person.settled_at):
        raise ValueError('积分协商零收益结算凭证与陪玩不匹配')
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != order.pk:
        return
    permit = permits[0]
    permit.closed_at = timezone.now(); permit.close_reason = 'ZERO_POINTS_REF_SETTLED'
    permit.zero_points_refund_person = person
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_points_refund_person'])


def close_for_zero_ordinary_addon(profile, person):
    """Consume a recovery opportunity after this person's total income is zero."""
    _atomic()
    if not tracking_enabled():
        return
    order = person.settlement.order
    assignment = order.providers.filter(provider_account_id=profile.account_id, provider_id=profile.user_id).first()
    addon_rows = person.settlement.addon_settlements.filter(segment_id=person.segment_id)
    if (person.amount != 0 or person.income_transaction_id or order.status != 'COMPLETED'
            or person.segment.attribution.order_id != order.pk or person.segment.provider_account_id != profile.account_id
            or not assignment or assignment.settled_at != person.settled_at
            or addon_rows.exclude(amount=0).exists() or addon_rows.exclude(income_transaction__isnull=True).exists()):
        raise ValueError('普通退款及加购的零收益结算凭证与陪玩不匹配')
    permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, closed_at__isnull=True))
    if len(permits) > 1:
        raise ValueError('陪玩存在多个未关闭的补差机会')
    if not permits or permits[0].order_id != order.pk:
        return
    permit = permits[0]
    permit.closed_at = timezone.now()
    permit.close_reason = 'ZERO_ADDON_SETTLED'
    permit.zero_ordinary_addon_person = person
    permit.save(update_fields=['closed_at', 'close_reason', 'zero_ordinary_addon_person'])


def cancel_unstarted(order, *, account_ids=None, reason='CANCELLED_UNSTARTED'):
    """An unstarted cancelled/repooled order returns its unused permit."""
    _atomic()
    if not tracking_enabled() or order.in_service_at:
        return
    if reason not in ('CANCELLED_UNSTARTED', 'TRANSFERRED_UNSTARTED'):
        raise ValueError('补差机会关闭原因无效')
    from users.models import EscortProfile
    from orders.models import OrderParticipationSegment
    from .models import ProviderReport
    active = RefundDepositRecoveryPermit.objects.filter(order=order, closed_at__isnull=True)
    if account_ids is not None:
        active = active.filter(account_id__in=list(account_ids))
    ids = list(active.values_list('profile_id', flat=True))
    for profile_id in sorted(ids):
        profile = EscortProfile.objects.select_for_update().get(pk=profile_id)
        permit = RefundDepositRecoveryPermit.objects.select_for_update().filter(
            order=order, profile_id=profile_id, closed_at__isnull=True).first()
        if permit:
            # A team member can submit entry evidence before the whole order
            # reaches IN_SERVICE; assess actual participation per person.
            reports = ProviderReport.objects.filter(order=order).filter(
                Q(provider_account_id=profile.account_id) |
                Q(provider_account_id__isnull=True, provider_id=profile.user_id))
            started = (reports.exclude(entry_image='').exclude(entry_image__isnull=True).exists()
                       or OrderParticipationSegment.objects.filter(attribution__order=order,
                           provider_account_id=profile.account_id, started_at__isnull=False).exists())
            if started:
                continue
            permit.closed_at = timezone.now(); permit.close_reason = reason
            permit.save(update_fields=['closed_at', 'close_reason'])
