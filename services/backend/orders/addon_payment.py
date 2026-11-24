"""Transactional add-on debit, limited by the enabled feature and order scope.

Payment records funding sources; provider income is released by settlement.
"""

from collections import defaultdict

from django.conf import settings
from django.db import DatabaseError
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from console.permissions import get_account_permissions
from engagement.funding_plan import AvailablePart, FundingPlanError, plan_payment
from engagement.models import PointFundingLot
from engagement.services import _funding_lot_parts
from stores.db import transaction
from wallet.models import Transaction
from wallet.services import get_wallet

from .addon_intents import _commission, _hash
from .addon_offers import AddonSelectionError, validate_selection
from .financial_lock import lock_order_money
from .models import Order, OrderAddonFundingAllocation, OrderAddonIntent, OrderAddonPayment
from .points_funding_plan import PointsFundingPlanError, plan_service_funding
from .settlement import ProviderShare


def payment_table_exists():
    """A persisted payment must remain visible after a feature is disabled."""
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    if OrderAddonPayment._meta.db_table in tables:
        return True
    if ('django_migrations' not in tables or MigrationRecorder.Migration.objects.using(connection.alias)
            .filter(app='orders', name='0175_orderaddonfundingallocation_orderaddonpayment_and_more').exists()):
        raise DatabaseError('加购付款凭证表缺失，须先核对数据库')
    return False


def _debit_evidence(order):
    query = Transaction.objects.filter(remark=f'订单 {order.order_no} 加购支付')
    if order.customer_account_id:
        return query.filter(wallet__account_id=order.customer_account_id)
    if order.customer_id:
        return query.filter(wallet__user_id=order.customer_id)
    return query


def guard_main_settlement(order):
    """Keep posted payments pending until add-on settlement is implemented."""
    from .settlement import SettlementError
    locked = transaction.get_connection().in_atomic_block
    receipts = OrderAddonPayment.objects.filter(intent__order=order)
    if locked:
        receipts = receipts.select_for_update()
    pending = payment_table_exists() and receipts.exists()
    if not pending:
        # The debit is deliberately not attached to the main order. Its
        # system-generated remark is independent evidence if a receipt or
        # even the entire receipt table has been lost.
        debits = _debit_evidence(order)
        pending = (debits.select_for_update() if locked else debits).exists()
    if pending:
        raise SettlementError('加购资金尚未纳入整单结算，请先核对')


def verify_order_debit_links(order, *, table_exists, lock=False):
    """Do not describe an orphaned successful debit as an unpaid intent."""
    debits_query = _debit_evidence(order)
    receipts_query = OrderAddonPayment.objects.filter(intent__order=order) if table_exists else None
    if lock:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('加购付款核对必须在订单事务中进行')
        debits_query = debits_query.select_for_update()
        if receipts_query is not None:
            receipts_query = receipts_query.select_for_update()
    debits = set(debits_query.values_list('pk', flat=True))
    receipts = set(receipts_query.values_list('buyer_transaction_id', flat=True)) if receipts_query is not None else set()
    if debits != receipts:
        raise ValidationError('加购支付流水与凭证不一致，请联系门店核对')


def _authorization(actor, intent):
    if (not actor or actor._state.db != transaction.get_connection().alias
            or not actor.is_active or not actor.can_login):
        raise ValidationError('账号不可用')
    if actor.account_type == ClubAccount.AccountType.BOSS and actor.pk == intent.buyer_id:
        return
    if (actor.account_type == ClubAccount.AccountType.STAFF
            and 'order:dispatch' in get_account_permissions(actor)):
        return
    raise ValidationError('无权支付该加购')


def _selection(intent):
    return {'addon_id': intent.addon_id, 'unit': intent.unit, 'quantity': intent.quantity,
            'configuration_version': intent.config_version, 'provider_account_id': intent.provider_id}


def _verify_intent(intent, order):
    """Bind the recorded amount, parties, sources and manual confirmation."""
    staff = intent.operator.account_type == ClubAccount.AccountType.STAFF
    if not staff and (intent.operator.account_type != ClubAccount.AccountType.BOSS or intent.operator_id != intent.buyer_id):
        raise ValidationError('加购申请人身份待核对')
    body = {**_selection(intent), 'request_id': intent.request_id, 'expected_amount': intent.amount,
            'gift_balance_amount': intent.gift, 'compensation_balance_amount': intent.compensation}
    if staff:
        confirmation = intent.confirmation
        if (not isinstance(confirmation, dict) or not confirmation.get('recorded_at')
                or any(not isinstance(confirmation.get(key), str) or len(confirmation[key]) < 2
                       for key in ('method', 'content', 'review_note'))):
            raise ValidationError('老板确认及客服核验记录待核对')
        body.update(confirmation_method=confirmation['method'], confirmation_content=confirmation['content'],
                    review_note=confirmation['review_note'])
        from console.models import AdminAuditLog
        audit = AdminAuditLog.objects.filter(resource='order-addon-intent', object_id=str(intent.pk),
                                              operator_account_id=intent.operator_id, status_code=200).first()
        if (not audit or not isinstance(audit.request_body, dict)
                or audit.request_body.get('order_id') != order.pk
                or audit.request_body.get('request_id') != intent.request_id
                or audit.request_body.get('selection') != _selection(intent)
                or audit.request_body.get('amount') != intent.amount
                or audit.request_body.get('gift') != intent.gift
                or audit.request_body.get('compensation') != intent.compensation
                or audit.request_body.get('confirmation') != confirmation):
            raise ValidationError('加购客服核验审计待核对')
    elif intent.confirmation or intent.condition_mode != 'NONE':
        raise ValidationError('加购购买条件待核对')
    if (intent.request_hash != _hash({'actor_id': intent.operator_id, 'staff': staff, 'body': body})
            or intent.buyer_id != order.customer_account_id or intent.segment.attribution.order_id != order.pk
            or intent.provider_id != intent.segment.provider_account_id
            or intent.amount != intent.unit_price * intent.quantity
            or intent.principal + intent.gift + intent.compensation != intent.amount):
        raise ValidationError('加购申请内容已变化，请重新核对')


def _commission_plan(intent, inviter_rate):
    snapshot = intent.commission_snapshot
    if not isinstance(snapshot, dict):
        raise ValidationError('陪玩抽成快照待核对')
    if snapshot.get('type') == 'PERCENT':
        rate = snapshot.get('rate')
        if type(rate) is not int or not 0 <= rate <= 100:
            raise ValidationError('陪玩抽成比例无效')
        fee = intent.amount * rate // 100
    elif snapshot.get('type') == 'FIXED':
        fixed, base = snapshot.get('fixed_rule_total'), snapshot.get('order_deal_amount')
        if type(fixed) is not int or fixed < 0 or type(base) is not int or base <= 0:
            raise ValidationError('主单固定抽成比例待核对')
        fee = min(intent.amount, intent.amount * fixed // base)
    else:
        raise ValidationError('陪玩抽成类型无效')
    share = ProviderShare(intent.amount, 'FIXED', 0, fee, intent.amount-fee)
    try:
        plan = plan_service_funding(deal_units=intent.amount, point_discount_units=0, shares=[share],
            inviter_rate=inviter_rate, principal_units=intent.principal, gift_units=intent.gift,
            compensation_units=intent.compensation)
    except PointsFundingPlanError as exc:
        raise ValidationError(str(exc)) from exc
    return fee, plan


def _plan_lots(wallet, intent, *, lock):
    query = PointFundingLot.objects.filter(account_id=intent.buyer_id, remaining__gt=0).order_by('pk')
    lots = list(query.select_for_update() if lock else query)
    if sum(lot.remaining for lot in lots) != wallet.balance:
        raise ValidationError('钱包余额与资金来源不一致，请先核对')
    if any(lot.kind not in PointFundingLot.Kind.values or
           (lot.kind == PointFundingLot.Kind.RECHARGE and lot.gift_remaining is None) for lot in lots):
        raise ValidationError('钱包资金来源待核对')
    parts = {lot.pk: _funding_lot_parts(lot, {}) for lot in lots}
    names = {'PREPAID': 'prepaid_balance', 'GIFT': 'gift_balance', 'COMPENSATION': 'compensation_balance'}
    available = [AvailablePart(lot.pk, kind, parts[lot.pk][name]) for lot in lots
                 for kind, name in names.items() if parts[lot.pk][name]]
    try:
        plan = plan_payment(available, intent.amount, gift=intent.gift, compensation=intent.compensation)
    except FundingPlanError as exc:
        raise ValidationError(str(exc)) from exc
    return lots, plan


def _consume_lots(lots, plan):
    selected = defaultdict(lambda: {'total': 0, 'gift': 0})
    for part in plan:
        selected[part.lot_id]['total'] += part.amount
        if part.kind == 'GIFT':
            selected[part.lot_id]['gift'] += part.amount
    for lot in lots:
        used = selected.get(lot.pk)
        if not used:
            continue
        lot.remaining -= used['total']
        fields = ['remaining']
        if lot.kind == PointFundingLot.Kind.RECHARGE:
            lot.gift_remaining -= used['gift']
            fields.append('gift_remaining')
        lot.save(update_fields=fields)


def verify_receipt(payment):
    """Reconcile a posted debit without using today's catalogue price."""
    intent, tx = payment.intent, payment.buyer_transaction
    if (tx.order_id is not None or tx.wallet.account_id != intent.buyer_id or tx.tx_type != 'PAY'
            or tx.status != 'SUCCESS' or tx.amount != -intent.amount or tx.reversal_of_id
            or tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount
            or tx.operator_account_id != payment.operator_id or payment.principal != intent.principal
            or not isinstance(payment.benefit_terms, dict)):
        raise ValidationError('加购支付流水待核对')
    fee, plan = _commission_plan(intent, payment.inviter_rate)
    if (payment.inviter_rate != 0 or payment.commission_fee != fee or payment.provider_income != plan.provider_income_units
            or payment.inviter_income != plan.inviter_income_units or payment.store_income != plan.store_income_units
            or payment.funding_required != plan.additional_store_funding_units
            or (payment.inviter_rate and not payment.inviter_id)):
        raise ValidationError('加购支付分账待核对')
    totals = defaultdict(int)
    rows = list(payment.allocations.select_related('lot'))
    for row in rows:
        allowed = {'PREPAID', 'GIFT'} if row.lot.kind == 'RECHARGE' else {row.lot.kind}
        if row.lot.account_id != intent.buyer_id or row.kind not in allowed:
            raise ValidationError('加购资金来源待核对')
        totals[row.kind] += row.amount
    if (totals['PREPAID'], totals['GIFT'], totals['COMPENSATION']) != (intent.principal, intent.gift, intent.compensation):
        raise ValidationError('加购支付来源合计不一致')
    refunds = list(payment.refund_records.select_related('buyer_transaction', 'provider_transaction__wallet'))
    if sum(row.refunded_amount for row in rows) != sum(row.amount for row in refunds):
        raise ValidationError('加购退款与原资金来源不一致')
    for refund in refunds:
        customer_tx = refund.buyer_transaction
        if (refund.order_id != intent.order_id or refund.proposal.payment_id != payment.pk
                or refund.proposal.status != 'ACCEPTED'
                or customer_tx.wallet_id != tx.wallet_id or customer_tx.tx_type != 'REFUND'
                or customer_tx.order_id is not None or customer_tx.status != 'SUCCESS' or customer_tx.amount != refund.amount
                or customer_tx.balance_after != customer_tx.balance_before + customer_tx.amount):
            raise ValidationError('加购退款流水待核对')
        people = list(refund.proposal.participants.select_related('provider_transaction__wallet'))
        deductions = list(refund.earning_deductions.select_related('payment__intent', 'segment', 'participant'))
        expected_decisions = {(intent.buyer_id, 'BOSS', True)} | {(row.account_id, 'PROVIDER', True) for row in people}
        decisions = set(refund.proposal.decisions.values_list('account_id', 'role', 'accepted'))
        if (not people or sum(row.burden for row in people) != refund.amount + refund.rounding_surplus
                or decisions != expected_decisions
                or sum(entry['refund_basis'] for person in people for entry in person.share_plan) != refund.amount
                or sum(row.addon_pending_deduction for row in people) != refund.addon_pending_deduction
                or sum(row.main_pending_deduction for row in people) != refund.main_pending_deduction
                or sum(row.wallet_deduction for row in people) != refund.wallet_deduction
                or sum(row.deposit_deduction for row in people) != refund.deposit_deduction
                or sum(row.amount for row in deductions) != refund.addon_pending_deduction
                or any(row.payment.intent.order_id != intent.order_id or row.participant.proposal_id != refund.proposal_id
                       or row.segment.provider_account_id != row.participant.account_id for row in deductions)
                or sum(refund.deposit_deficits.values_list('amount', flat=True)) != refund.deposit_deduction):
            raise ValidationError('加购退款收益或押金承担待核对')
        for person in people:
            provider_tx = person.provider_transaction
            main_parts = list(person.main_segment_deductions.select_related('segment__attribution'))
            if (sum(part.amount for part in main_parts) != person.main_pending_deduction
                    or any(part.segment.attribution.order_id != intent.order_id
                           or part.segment.provider_account_id != person.account_id for part in main_parts)):
                raise ValidationError('加购退款主单履约段扣减凭证异常')
            from .models import OrderParticipationSegment
            segments = {row.pk: row.provider_account_id for row in OrderParticipationSegment.objects.filter(
                attribution__order_id=intent.order_id, pk__in=[entry['segment_id'] for entry in person.share_plan])}
            if (sum(entry['basis'] for entry in person.share_plan) != person.basis
                    or len(segments) != len(person.share_plan)
                    or any(segments.get(entry['segment_id']) != person.account_id for entry in person.share_plan)
                    or sum(entry['burden'] for entry in person.share_plan) + person.excess_burden != person.burden
                    or any(not (0 <= entry['refund_basis'] <= entry['basis'] and
                                0 <= entry['burden'] <= entry['burden_capacity'] <= entry['basis'])
                           for entry in person.share_plan)
                    or (person.excess_burden and sum(entry['burden'] for entry in person.share_plan) !=
                        sum(entry['burden_capacity'] for entry in person.share_plan))
                    or person.burden != person.addon_pending_deduction + person.main_pending_deduction +
                    person.wallet_deduction + person.deposit_deduction
                    or sum(row.amount for row in deductions if row.participant_id == person.pk) != person.addon_pending_deduction
                    or sum(refund.deposit_deficits.filter(account_id=person.account_id).values_list('amount', flat=True)) != person.deposit_deduction
                    or (not person.wallet_deduction and provider_tx)
                    or (person.wallet_deduction and (not provider_tx or provider_tx.tx_type != 'REFUND'
                        or provider_tx.status != 'SUCCESS' or provider_tx.amount != -person.wallet_deduction
                        or provider_tx.wallet.account_id != person.account_id or provider_tx.order_id is not None
                        or provider_tx.balance_after != provider_tx.balance_before + provider_tx.amount))):
                raise ValidationError('加购退款个人承担流水待核对')
    return payment



def _validate_unpaid(order, intent, wallet, *, lock):
    """Shared current-state check; the caller controls locking and mutation."""
    if (order.settled_at or not order.customer_account_id
            or order.customer_account.business_cycle != order.business_cycle):
        raise ValidationError('订单状态已变化，请重新核对')
    try:
        config, option, segment, amount = validate_selection(order, _selection(intent), lock=lock)
        from .points_business_access import permits as points_permits
        round_review = bool(lock and order.refund_records.count() >= 4
            and points_permits(order, phase='payment', kind='transfer_addon'))
        if round_review:
            from .points_round_funding import verify_active_segment_locked
            verify_active_segment_locked(order, segment)
        snapshot = _commission(segment, lock=lock, allow_refund_review=round_review)
    except AddonSelectionError as exc:
        raise ValidationError(str(exc)) from exc
    if (amount != intent.amount or option.price != intent.unit_price or segment.pk != intent.segment_id
            or config.addon.name != intent.addon_name or config.conditions != intent.condition_snapshot
            or config.condition_mode != intent.condition_mode or snapshot != intent.commission_snapshot):
        raise ValidationError('加购价格、人员或条件已变化，请重新核验并确认')
    from .aftersale_scope import applies_to
    if any(applies_to(case, intent.provider_id) for case in order.aftersales.filter(status='OPEN')):
        raise ValidationError('该陪玩的售后尚未处理，请先核对')
    fee, plan = _commission_plan(intent, 0)
    if not wallet or not wallet.is_active or wallet.balance < intent.amount:
        raise ValidationError('钱包不可用或余额不足')
    lots, allocations = _plan_lots(wallet, intent, lock=lock)
    return fee, plan, lots, allocations



def _check_benefit_config(order, *, lock):
    from engagement.models import ClubOperationConfig, VipTier
    from users.models import BossType
    configs = ClubOperationConfig.objects
    if lock:
        configs = configs.select_for_update()
    operation = configs.filter(pk=1).first()
    if not operation:
        raise ValidationError('会员配置尚未初始化')
    account = order.customer_account
    boss_type = BossType.objects.filter(pk=account.boss_type_id, is_active=True).first() if account.boss_type_id else None
    tiers = VipTier.objects
    if lock:
        tiers = tiers.select_for_update()
    tier = tiers.filter(boss_type_id=boss_type.pk, active=True).first() if boss_type else None
    rate = tier.points_rate if tier else operation.base_points_rate
    if type(rate) is not int or not 0 <= rate <= 1000:
        raise ValidationError('门店会员积分倍率无效')


def _preview_funded_order(intent_id, *, actor):
    """Recheck existing funding without posting a debit or a funding release."""
    from django.db import DatabaseError
    from django.db.models import Q
    from wallet.models import Wallet
    from .financial_lock import lock_existing_order_money
    from .settlement import SettlementError
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .models import (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                         ReservationPointsCancelFundingRelease)
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from .addon_funding_release import addon_release_totals
    from .points_funding_allocation import PointsFundingAllocationError, plan_release
    from .service_funding_plan import plan_verified_ordinary_order
    # The payroll barrier must precede the order lock; _data may already be
    # inside an intent-creation transaction holding that lock. Remain cautious.
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        return False
    try:
        with transaction.atomic():
            initial = OrderAddonIntent.objects.filter(pk=intent_id).values('order_id').first()
            if not initial:
                return False
            order_id = Order.objects.select_for_update().values_list('pk', flat=True).get(pk=initial['order_id'])
            # Never create a mutex from a read. Missing lock means unknown.
            lock_existing_order_money()
            order = Order.objects.select_for_update().select_related('customer_account', 'service').get(pk=order_id)
            if not ordinary_addon_eligible(order, allow_unpaid=True, payment_intent_id=intent_id):
                return False
            actor = ClubAccount.objects.select_for_update().filter(pk=getattr(actor, 'pk', None)).first()
            intent = OrderAddonIntent.objects.select_for_update().select_related('segment__attribution', 'operator').get(pk=intent_id, order=order)
            if OrderAddonPayment.objects.select_for_update().filter(intent=intent).exists():
                return False
            _authorization(actor, intent)
            _verify_intent(intent, order)
            verify_order_debit_links(order, table_exists=True, lock=True)
            wallets = list(Wallet.objects.select_for_update().filter(
                Q(account_id=intent.buyer_id) | Q(user_id=order.customer_id))[:2])
            if (len(wallets) != 1 or wallets[0].account_id != intent.buyer_id
                    or wallets[0].user_id != order.customer_id):
                return False
            _validate_unpaid(order, intent, wallets[0], lock=True)
            _check_benefit_config(order, lock=True)
            # This is the same original-order and platform/tenant receipt
            # evidence used by the payment's funding-release path. Both run
            # under order -> money -> receipt locks; no release is posted here.
            main = plan_verified_ordinary_order(order.pk)
            from .addon_funding import include_paid_addons
            # Existing purchases contribute their own verified funding needs.
            # Comparing against the main order alone rejects a valid later
            # payment whenever an earlier add-on needed store funding.
            combined = include_paid_addons(order, main)
            allocations = _verified_allocations_for_locked_order(order)
            if (OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists()
                    or ReservationPointsCancelFundingRelease.objects.filter(allocation__in=allocations).exists()
                    or OrderPointsTransferFundingRelease.objects.filter(allocation__in=allocations).exists()):
                return False
            released = addon_release_totals(allocations)
            snapshot = [[row.pk, row.amount, released[row.pk]] for row in allocations]
            used = sum(amount - returned for _, amount, returned in snapshot)
            if used > combined.additional_store_funding_units:
                return False
            plan_release(allocations=snapshot, amount=0)
            return True
    except (ValidationError, AddonSelectionError, PointsFundingPlanError, PointsFundingAllocationError,
            SettlementError, DatabaseError, Order.DoesNotExist, OrderAddonIntent.DoesNotExist):
        return False

def payment_available(intent, *, actor):
    """Read-only, conservative snapshot, not a promise that payment will succeed."""
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .models import OrderPointsFundingAllocation
    from django.db.models import Q
    from wallet.models import Wallet
    order = Order.objects.select_related('customer_account', 'service').filter(pk=intent.order_id).first()
    if not order:
        return False
    from .points_service_payment import needs_integrated_processing
    from .ordinary_transfer_capability import enabled as transferred_enabled
    from .models import OrderTransferNegotiationEvidence, OrderOriginalShareTransfer
    from .settlement import SettlementError
    if transferred_enabled() and (OrderTransferNegotiationEvidence.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        try:
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                from .financial_lock import lock_existing_order_money
                lock_existing_order_money()
                from .ordinary_transfer_capability import permits as transferred_permits
                if not transferred_permits(locked_order):
                    return False
                locked_intent = OrderAddonIntent.objects.select_for_update().select_related(
                    'segment__attribution', 'operator').get(pk=intent.pk, order=locked_order)
                if OrderAddonPayment.objects.filter(intent=locked_intent).exists():
                    return False
                _authorization(actor, locked_intent)
                _verify_intent(locked_intent, locked_order)
                verify_order_debit_links(locked_order, table_exists=True, lock=True)
                wallets = list(Wallet.objects.select_for_update().filter(
                    Q(account_id=locked_intent.buyer_id) | Q(user_id=locked_order.customer_id))[:2])
                if (len(wallets) != 1 or wallets[0].account_id != locked_intent.buyer_id
                        or wallets[0].user_id != locked_order.customer_id):
                    return False
                _check_benefit_config(locked_order, lock=True)
                _validate_unpaid(locked_order, locked_intent, wallets[0], lock=True)
                return True
        except (ValidationError, AddonSelectionError, PointsFundingPlanError, SettlementError,
                DatabaseError, Order.DoesNotExist, OrderAddonIntent.DoesNotExist):
            return False
    if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(order)):
        # Preview cannot create a missing mutex or lock the payroll barrier
        # after an outer caller has already taken order locks.
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            return False
        from .financial_lock import lock_existing_order_money
        from .points_business_access import service_order
        from .settlement import SettlementError
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order.pk)
                lock_existing_order_money()
                from .models import OrderOriginalShareTransfer
                transferred_followup = (order.refund_records.count() >= 1
                    and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
                if transferred_followup:
                    if order.refund_records.count() >= 3:
                        from .points_transfer_refund_access import post_third_addon_payment
                        scope = post_third_addon_payment(order.pk, intent.pk)
                    else:
                        from .points_transfer_refund_access import post_followup_addon_payment
                        scope = post_followup_addon_payment(order.pk, intent.pk)
                else:
                    scope = service_order(order.pk, phase='payment', addon=True)
                with scope:
                    if OrderPointsFundingAllocation.objects.filter(
                            Q(redemption__order=order) | Q(service_order=order)).exists():
                        if not transferred_followup:
                            from .points_funding_evidence import verify_order_funding
                            verify_order_funding(order.pk)
                    locked_intent = OrderAddonIntent.objects.select_for_update().select_related(
                        'segment__attribution', 'operator').get(pk=intent.pk, order=order)
                    if OrderAddonPayment.objects.filter(intent=locked_intent).exists():
                        return False
                    _authorization(actor, locked_intent)
                    _verify_intent(locked_intent, order)
                    verify_order_debit_links(order, table_exists=True, lock=True)
                    wallets = list(Wallet.objects.select_for_update().filter(
                        Q(account_id=locked_intent.buyer_id) | Q(user_id=order.customer_id))[:2])
                    if (len(wallets) != 1 or wallets[0].account_id != locked_intent.buyer_id
                            or wallets[0].user_id != order.customer_id):
                        return False
                    _check_benefit_config(order, lock=True)
                    _validate_unpaid(order, locked_intent, wallets[0], lock=True)
                    return True
        except (ValidationError, AddonSelectionError, PointsFundingPlanError, SettlementError,
                DatabaseError, Order.DoesNotExist, OrderAddonIntent.DoesNotExist):
            return False
    try:
        if not (getattr(settings, 'ADDON_PAYMENT_INTERNAL_TESTING', False)
                or ordinary_addon_eligible(order, allow_unpaid=True, payment_intent_id=intent.pk)):
            return False
        _authorization(actor, intent)
        _verify_intent(intent, order)
        verify_order_debit_links(order, table_exists=True)
        has_funding = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order)).exists()
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_models import PayrollEventBarrier
            from stores.context import current_database, current_store
            connection = transaction.get_connection()
            store = current_store()
            if (store is None or store.database_alias != current_database() or connection.alias != current_database()
                    or (getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and connection.alias == 'default')
                    or connection.vendor != 'mysql'
                    or PayrollEventBarrier._meta.db_table not in connection.introspection.table_names()
                    or not PayrollEventBarrier.objects.filter(pk=1).exists()):
                return False
        # get_wallet() may backfill identity. Preview never writes, and fails
        # closed for incomplete or conflicting identity records.
        wallets = list(Wallet.objects.filter(Q(account_id=intent.buyer_id) | Q(user_id=order.customer_id))[:2])
        if (len(wallets) != 1 or wallets[0].account_id != intent.buyer_id
                or wallets[0].user_id != order.customer_id):
            return False
        _check_benefit_config(order, lock=False)
        _validate_unpaid(order, intent, wallets[0], lock=False)
        if has_funding:
            # The payment transaction rechecks and rebalances the real
            # post-refund funding version under its order and receipt locks.
            from .negotiation_zero import marked_execution_id
            if ((order.refund_records.count() == 1 or marked_execution_id(order))
                    and ordinary_addon_eligible(order, allow_unpaid=True, payment_intent_id=intent.pk)):
                return True
            return _preview_funded_order(intent.pk, actor=actor)
        return True
    except (ValidationError, AddonSelectionError, PointsFundingPlanError):
        return False

def pay(intent_id, *, actor):
    from .points_service_payment import needs_integrated_processing
    initial = OrderAddonIntent.objects.filter(pk=intent_id).select_related('order', 'provider').first()
    lifecycle = None
    if initial and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
        lifecycle = initial.provider
        try:
            ensure_provider_lifecycle(lifecycle)
        except ProviderLifecycleError as exc:
            raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
    with transaction.atomic():
        if lifecycle:
            from club_accounts.provider_lifecycle import lock_provider_operation
            try:
                lock_provider_operation(lifecycle)
            except ProviderLifecycleError as exc:
                raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
        if (initial and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                and needs_integrated_processing(initial.order)):
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            from .points_business_access import service_order
            from .models import OrderOriginalShareTransfer
            transferred_followup = (initial.order.refund_records.count() >= 1
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=initial.order).exists())
            if transferred_followup:
                if initial.order.refund_records.count() >= 3:
                    from .points_transfer_refund_access import post_third_addon_payment
                    scope = post_third_addon_payment(initial.order_id, initial.pk)
                else:
                    from .points_transfer_refund_access import post_followup_addon_payment
                    scope = post_followup_addon_payment(initial.order_id, initial.pk)
            else:
                scope = service_order(initial.order_id, phase='payment', addon=True)
            with scope:
                return _pay(intent_id, actor=actor, lifecycle=lifecycle)
        return _pay(intent_id, actor=actor, lifecycle=lifecycle)


def _pay(intent_id, *, actor, lifecycle=None):
    """Debit a verified, eligible add-on intent once under the order locks."""
    initial = OrderAddonIntent.objects.filter(pk=intent_id).values('order_id').first()
    if not initial:
        raise ValidationError('加购申请不存在')
    with transaction.atomic():
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        order_id = Order.objects.select_for_update().values_list('pk', flat=True).get(pk=initial['order_id'])
        lock_order_money()
        order = Order.objects.select_for_update().select_related('service', 'customer_account').get(pk=order_id)
        from .points_addon_receipt_usage import prelock as prelock_addon_receipts
        prelock_addon_receipts(order)
        from .ordinary_addon_production import eligible as ordinary_addon_eligible
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        from .points_business_access import permits
        formal = permits(order, phase='payment', kind='addon')
        if not (formal or getattr(settings, 'ADDON_PAYMENT_INTERNAL_TESTING', False)
                or ordinary_addon_eligible(order, allow_unpaid=True, payment_intent_id=intent_id) or ordinary_transfer_permits(order)):
            raise ValidationError('加购付款与结算尚未开放')
        if not actor or actor._state.db != transaction.get_connection().alias:
            raise ValidationError('账号不属于当前门店')
        actor = ClubAccount.objects.select_for_update().filter(pk=actor.pk).first()
        intent = OrderAddonIntent.objects.select_for_update().select_related(
            'segment__attribution', 'operator').get(pk=intent_id, order=order)
        _authorization(actor, intent)
        _verify_intent(intent, order)
        verify_order_debit_links(order, table_exists=True, lock=True)
        existing = OrderAddonPayment.objects.select_for_update().select_related(
            'intent', 'buyer_transaction__wallet').filter(intent=intent).first()
        if existing:
            return verify_receipt(existing)
        from .points_transfer_refund_access import permits as transfer_permits
        paid_before = None
        if (formal and order.refund_records.exists() and order.refund_records.count() < 3
                and transfer_permits(order, phase='payment')):
            from .points_voluntary_paid_evidence import capture_before_payment_locked
            paid_before = capture_before_payment_locked(order)
        if lifecycle:
            if (intent.provider_id != lifecycle.pk or intent.segment.provider_account_id != lifecycle.pk
                    or intent.segment.provider_business_cycle != lifecycle.business_cycle
                    or not ClubAccount.objects.filter(pk=lifecycle.pk, account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True, business_cycle=lifecycle.business_cycle).exists()):
                raise ValidationError('加购陪玩账号或履约段已变化，请重新确认')
        wallet = get_wallet(account_id=intent.buyer_id, user_id=order.customer_id, for_update=True, create=False)
        fee, plan, lots, allocations = _validate_unpaid(order, intent, wallet, lock=True)
        inviter_rate = 0
        _consume_lots(lots, allocations)
        before = wallet.balance
        wallet.balance -= intent.amount
        wallet.save(update_fields=['balance', 'updated_at'])
        debit = Transaction.objects.create(wallet=wallet, amount=-intent.amount, tx_type='PAY',
            balance_before=before, balance_after=wallet.balance, operator_account=actor,
            remark=f'订单 {order.order_no} 加购支付')
        paid_at = debit.created_at
        from engagement.funding_wallet import snapshot_payment_funding_terms
        benefit_terms = snapshot_payment_funding_terms(intent.buyer_id, paid_at)
        payment = OrderAddonPayment.objects.create(intent=intent, buyer_transaction=debit, operator=actor,
            inviter_id=order.inviter_account_id, inviter_rate=inviter_rate, commission_fee=fee,
            provider_income=plan.provider_income_units, inviter_income=plan.inviter_income_units,
            store_income=plan.store_income_units, principal=intent.principal,
            funding_required=plan.additional_store_funding_units, benefit_terms=benefit_terms, paid_at=paid_at)
        OrderAddonFundingAllocation.objects.bulk_create([OrderAddonFundingAllocation(payment=payment,
            lot_id=part.lot_id, kind=part.kind, amount=part.amount) for part in allocations])
        verify_receipt(payment)
        from .addon_funding_release import record_payment_funding_release
        from .ordinary_post_refund_transfer import refresh_for_event
        if not refresh_for_event(order, source_kind='ADDON_PAYMENT', source_id=payment.pk):
            record_payment_funding_release(order, payment)
        if paid_before is not None:
            from .points_voluntary_paid_evidence import record_payment_locked
            paid_evidence = record_payment_locked(payment, paid_before)
            if (paid_evidence and order.status == order.Status.IN_SERVICE
                    and order.refund_records.count() == 2):
                from .points_followup_paid_handover import transition_payment_in_service_locked
                transition_payment_in_service_locked(order, paid_evidence)
        if actor.account_type == ClubAccount.AccountType.STAFF:
            from console.models import AdminAuditLog
            AdminAuditLog.objects.create(operator_account=actor, operator_name=(actor.nickname or actor.username)[:150],
                method='POST', path='/api/admin/orders/addons/pay/', resource='order-addon-payment',
                object_id=str(payment.pk), request_body={'order_id': order.pk, 'intent_id': intent.pk,
                    'buyer_id': intent.buyer_id, 'amount': intent.amount}, status_code=200)
        from site_messages.addon_payment_notice import record as record_addon_payment_notice
        record_addon_payment_notice(payment)
        from wallet.notifier import notify_wallet_update
        transaction.on_commit(lambda: notify_wallet_update(wallet))
        return payment
