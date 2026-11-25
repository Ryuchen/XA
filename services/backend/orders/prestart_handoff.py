"""Transfer an assigned, not-yet-started order without recording fulfillment."""
from django.conf import settings
from django.utils import timezone
from copy import copy
from contextlib import ExitStack
import hashlib
import json

from club_accounts.models import ClubAccount
from stores.db import transaction
from users.models import EscortProfile

from .models import Order, OrderPrestartHandoff, OrderProvider, OrderStatusLog, Reservation
from .state_machine import IllegalTransitionError, log_only


def _snapshot(row):
    return {name: getattr(row, name) for name in ('provider_account_id', 'provider_name_snapshot',
        'settlement_base', 'provider_income', 'commission_type', 'commission_rate',
        'commission_fixed', 'game_commission_snapshot')}


def _legacy_review_key(order):
    payload = {'order_id': order.pk, 'service_id': order.service_id,
               'game_commission_snapshot': order.game_commission_snapshot,
               'provider_requirement_snapshot': order.provider_requirement_snapshot}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def legacy_minimum_status(order):
    """Report whether this specific old snapshot needs documented review."""
    from .paid_service_scope import enabled as paid_scope_enabled
    if paid_scope_enabled():
        from .models import OrderPaidServiceScope
        if OrderPaidServiceScope.objects.filter(order=order).exists():
            return {'required': False}
    scope = (order.game_commission_snapshot or {}).get('service_scope')
    if (not isinstance(scope, dict) or order.provider_requirement_snapshot or
            {'catalog_family', 'required_level_id', 'required_level_rank'} <= set(scope)):
        return {'required': False}
    key = _legacy_review_key(order)
    for event in OrderPrestartHandoff.objects.filter(order=order).order_by('-pk'):
        review = event.new_snapshot.get('legacy_minimum_review') if isinstance(event.new_snapshot, dict) else None
        if not isinstance(review, dict) or review.get('key') != key:
            continue
        choice, level_id, rank = review.get('choice'), review.get('level_id'), review.get('level_rank')
        valid_level = ((choice == 'NONE' and level_id is None and rank is None) or
                       (choice == 'LEVEL' and type(level_id) is int and level_id > 0 and type(rank) is int and rank >= 0))
        if (valid_level and isinstance(review.get('original_requirement'), str) and review['original_requirement'].strip()
                and isinstance(review.get('evidence'), str) and review['evidence'].strip()
                and type(review.get('operator_id')) is int and review['operator_id'] > 0):
            return {'required': False, 'review': review}
    return {'required': True}


def _legacy_minimum(order, submitted, operator):
    status = legacy_minimum_status(order)
    if status.get('review'):
        return status['review']
    if not status['required'] or not isinstance(submitted, dict):
        raise IllegalTransitionError('历史订单未保存原最低等级，请先核对')
    evidence, description = submitted.get('evidence'), submitted.get('original_requirement')
    choice = submitted.get('choice')
    if (choice not in ('NONE', 'LEVEL') or not isinstance(evidence, str) or not 3 <= len(evidence.strip()) <= 500
            or not isinstance(description, str) or not 2 <= len(description.strip()) <= 120):
        raise IllegalTransitionError('请填写原等级要求、核对依据并确认最低等级')
    level_id = submitted.get('level_id')
    level = None
    if choice == 'LEVEL':
        if type(level_id) is not int or level_id < 1:
            raise IllegalTransitionError('请选择已核对的最低等级')
        from users.models import EscortLevel
        level = EscortLevel.objects.select_for_update().filter(pk=level_id, is_active=True).first()
        if not level:
            raise IllegalTransitionError('所选最低等级已停用或不存在，请重新核对')
    elif level_id is not None:
        raise IllegalTransitionError('无最低等级时不能选择等级')
    return {'key': _legacy_review_key(order), 'choice': choice, 'level_id': level.pk if level else None,
            'level_name': level.name if level else '', 'level_rank': level.sort_order if level else None,
            'original_requirement': description.strip(), 'evidence': evidence.strip(), 'operator_id': operator.pk}


def _purchased_service(order, service, *, legacy_review=None, operator=None, verify_commission=True):
    """Reconstruct only facts explicitly captured at payment."""
    from .game_commission import service_scope
    from .paid_service_scope import enabled as paid_scope_enabled
    from users.qualifications import service_requirement
    original_requirement = order.provider_requirement_snapshot or {}
    snapshot = order.game_commission_snapshot or {}
    if not isinstance(snapshot, dict) or not isinstance(original_requirement, dict):
        raise IllegalTransitionError('原订单资格快照无效，请先核对')
    paid_scope = None
    if paid_scope_enabled():
        from django.core.exceptions import ObjectDoesNotExist
        try:
            paid_scope = order.paid_service_scope.scope
        except ObjectDoesNotExist:
            pass
    if not snapshot and paid_scope is None:
        if paid_scope_enabled():
            raise IllegalTransitionError('历史订单未保存原服务范围，请先核对')
        current = service_requirement(service) or {}
        keys = ('game_category_id', 'provider_kind', 'platform', 'security_level', 'grade')
        if bool(current) != bool(original_requirement) or any(current.get(key) != original_requirement.get(key) for key in keys):
            raise IllegalTransitionError('服务资格与原订单快照不一致，请先核对')
        return service, None
    game_scope = snapshot.get('service_scope') if snapshot else None
    scope = paid_scope if paid_scope is not None else game_scope
    expected = set(service_scope(service))
    optional = {'catalog_family', 'required_level_id', 'required_level_rank'}
    if not isinstance(scope, dict) or set(scope) not in (expected, expected - optional) or scope.get('service_id') != service.pk:
        raise IllegalTransitionError('原订单服务范围快照不完整，请先核对')
    if snapshot and (not isinstance(game_scope, dict) or set(game_scope) not in (expected, expected - optional) or
            any(scope.get(key) != value for key, value in game_scope.items())):
        raise IllegalTransitionError('原订单游戏抽成和服务范围快照不一致，请先核对')
    review = None
    if not optional <= set(scope) and not original_requirement:
        review = _legacy_minimum(order, legacy_review, operator)
    numeric = ('game_category_id', 'gameplay_id', 'service_category_id')
    if any(scope[key] is not None and (type(scope[key]) is not int or scope[key] < 1) for key in numeric):
        raise IllegalTransitionError('原订单服务范围快照无效，请先核对')
    if any(not isinstance(scope[key], str) for key in ('provider_kind', 'platform', 'security_level',
                                                       'grade', 'experience_kind', 'commission_policy')):
        raise IllegalTransitionError('原订单服务范围快照无效，请先核对')
    if any(type(scope[key]) is not bool for key in ('is_addon', 'is_active', 'commission_override')):
        raise IllegalTransitionError('原订单服务范围快照无效，请先核对')
    if snapshot and scope['game_category_id'] != snapshot.get('game_category_id'):
        raise IllegalTransitionError('原订单游戏分类快照不一致，请先核对')
    if optional <= set(scope):
        level_id, rank = scope['required_level_id'], scope['required_level_rank']
        if (not isinstance(scope['catalog_family'], str) or
                (level_id is None) != (rank is None) or
                (level_id is not None and (type(level_id) is not int or level_id < 1 or type(rank) is not int or rank < 0))):
            raise IllegalTransitionError('原订单服务等级快照无效，请先核对')
    if verify_commission and snapshot and service.commission_override != scope['commission_override']:
        raise IllegalTransitionError('本店服务独立抽成配置已变化，请先核对')
    purchased = copy(service)
    purchased._state = copy(service._state)
    purchased._state.fields_cache = {}
    for key in ('game_category_id', 'gameplay_id', 'provider_kind', 'platform', 'security_level',
                'service_category_id', 'is_addon', 'experience_kind', 'commission_policy'):
        setattr(purchased, key, scope[key])
    purchased.provider_grade = scope['grade']
    # A paid order retains its purchase-time service state even if the
    # catalog entry is subsequently disabled.
    purchased.is_active = scope['is_active']
    # The old snapshot did not save catalog_family; do not authorize a
    # different product merely because today's family happens to match.
    if optional <= set(scope):
        from types import SimpleNamespace
        purchased.catalog_family = scope['catalog_family']
        purchased.required_level_id = scope['required_level_id']
        purchased._state.fields_cache['required_level'] = (SimpleNamespace(sort_order=scope['required_level_rank'])
                                                            if scope['required_level_id'] is not None else None)
    elif any(service_scope(service).get(key) != value for key, value in scope.items()):
        purchased.catalog_family = ''
    if review is not None:
        from types import SimpleNamespace
        purchased.required_level_id = review['level_id']
        purchased._state.fields_cache['required_level'] = (SimpleNamespace(sort_order=review['level_rank'])
                                                            if review['level_id'] is not None else None)
    from django.core.exceptions import ObjectDoesNotExist
    try:
        current = service_requirement(purchased) or {}
    except ObjectDoesNotExist as exc:
        raise IllegalTransitionError('原订单服务范围引用已失效，请先核对') from exc
    keys = ('game_category_id', 'provider_kind', 'platform', 'security_level', 'grade')
    if bool(current) != bool(original_requirement) or any(current.get(key) != original_requirement.get(key) for key in keys):
        raise IllegalTransitionError('原订单资格与服务范围快照不一致，请先核对')
    return purchased, review


def _fixed_for_assignment(order, active, assignment, total):
    """Split one current fixed rule across the original participant slots."""
    total = min(total, order.amount)
    if not order.amount:
        return 0
    events = list(OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').values_list('old_provider_id', 'new_provider_id'))
    original_ids = dict(order.providers.values_list('provider_account_id', 'pk'))
    def root_id(account_id):
        for old_id, new_id in events:
            if new_id == account_id:
                account_id = old_id
        return original_ids.get(account_id, 0)
    rows = list(active.values())
    amounts = {row.provider_account_id:total * row.settlement_base // order.amount for row in rows}
    remaining = total - sum(amounts.values())
    ranked = sorted(rows, key=lambda row: (-(total * row.settlement_base % order.amount), root_id(row.provider_account_id)))
    for row in ranked[:remaining]:
        amounts[row.provider_account_id] += 1
    return amounts[assignment.provider_account_id]


def _commands(data):
    rows = data.get('transfers')
    if not isinstance(rows, list) or not rows or len(rows) > 2:
        raise IllegalTransitionError('请选择需要更换的陪玩')
    commands = []
    for row in rows:
        if not isinstance(row, dict):
            raise IllegalTransitionError('更换陪玩的数据无效')
        old, new = row.get('old_provider_id'), row.get('new_provider_id')
        if type(old) is not int or type(new) is not int or old < 1 or new < 1 or old == new:
            raise IllegalTransitionError('请选择不同的原陪玩和接替陪玩')
        if row.get('split_type', 'PERCENT') not in ('PERCENT', 'FIXED') or row.get('split_value', 0) != 0 or row.get('penalty_amount', 0) != 0:
            raise IllegalTransitionError('服务开始前更换陪玩不能分配已履约收益或在此处罚')
        commands.append((old, new))
    if len({x[0] for x in commands}) != len(commands) or len({x[1] for x in commands}) != len(commands):
        raise IllegalTransitionError('同一陪玩不能重复转出或转入')
    reason = data.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
        raise IllegalTransitionError('请填写更换原因')
    return commands, reason.strip()


def reassign(order_id, data, *, operator, operator_user=None):
    """Atomically preserve paid facts and give each replacement a fresh rule."""
    from .direct_payment_models import DirectPaymentIntent
    from .financial_lock import lock_order_money
    from .services import ensure_provider_dispatch_locks, lock_provider_dispatch, refresh_escort_status
    from .team_rules import required_team_size

    commands, reason = _commands(data)
    new_ids = [new for _, new in commands]
    from .transfer_lifecycle import capture as capture_transfer_lifecycle, lock as lock_transfer_lifecycle
    lifecycle = capture_transfer_lifecycle(commands)
    ensure_provider_dispatch_locks(new_ids)
    with transaction.atomic(), ExitStack() as scopes:
        lock_transfer_lifecycle(lifecycle)
        lifecycle_cycles = ({account.pk: account.business_cycle for account in lifecycle[0]}
                            if lifecycle is not None else None)
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.GRABBED or order.in_service_at or order.payment_status != Order.PaymentStatus.PAID:
            raise IllegalTransitionError('订单已开始、取消或付款状态变化，请刷新后核对')
        if order.is_gift_snapshot or order.settled_at or DirectPaymentIntent.objects.filter(order=order).exists():
            raise IllegalTransitionError('该订单暂不支持开始前改派，请先核对')
        from .points_service_payment import needs_integrated_processing
        points_order = needs_integrated_processing(order)
        if points_order:
            from .points_business_access import configured, service_order
            if configured():
                from rest_framework.exceptions import ValidationError
                try:
                    scopes.enter_context(service_order(order.pk, phase='assignment'))
                except ValidationError as exc:
                    raise IllegalTransitionError('积分订单改派条件或资金记录待核对') from exc
            from .points_checkout_test import assignment_enabled
            if not assignment_enabled():
                raise IllegalTransitionError('积分订单服务前改派尚未开放')
            if order.refund_records.exists():
                raise IllegalTransitionError('积分订单已有退款，改派资金重算尚未接通')
            from .points_funding_plan import PointsFundingPlanError, plan_verified_order
            try:
                plan_verified_order(order.pk)
                from .points_funding_evidence import verify_order_funding
                verify_order_funding(order.pk)
            except PointsFundingPlanError as exc:
                raise IllegalTransitionError('积分订单原陪玩收益待核对') from exc
        from .models import OrderSegmentFinancialCoverage
        from .segment_financial_capture import subsidy_capture_enabled
        if (OrderSegmentFinancialCoverage.objects.filter(attribution__order=order, source='PLATFORM_SUBSIDY').exists()
                and not subsidy_capture_enabled()):
            raise IllegalTransitionError('安全箱接单快照暂不可用，请先恢复记录后再改派')
        from .segment_financial_capture import enabled as segment_capture_enabled, enforcement_enabled as segment_enforcement_enabled
        if segment_enforcement_enabled() and not segment_capture_enabled():
            from .models import OrderSegmentFinancialCoverage
            if OrderSegmentFinancialCoverage.objects.filter(attribution__order=order).exists():
                raise IllegalTransitionError('分段成交记录暂不可用，请先恢复记录后再改派')
        from .refunds import negotiated_aftersale_blocks_completion
        if negotiated_aftersale_blocks_completion(order) or order.aftersales.filter(status='OPEN').exists():
            raise IllegalTransitionError('售后处理中，请先处理售后再改派')
        lock_order_money()
        # Paid-order creation takes service before dispatch capacity. Reuse
        # that order to avoid holding a provider mutex while waiting for a
        # service row held by a simultaneous checkout for the same provider.
        from .models import ServiceItem
        catalog_service = ServiceItem.objects.select_for_update().get(pk=order.service_id)
        lock_provider_dispatch(new_ids)
        assignments = list(order.providers.select_for_update().order_by('pk'))
        if not assignments and required_team_size(order) == 1 and order.provider_id and order.provider_account_id:
            # Older single-provider and some reservation orders have only the
            # order-level snapshot. Materialize it under the same order lock.
            assignments = [OrderProvider.objects.create(order=order, provider_id=order.provider_id,
                provider_account_id=order.provider_account_id, provider_name_snapshot=order.provider_name_snapshot,
                settlement_base=order.amount, provider_income=order.provider_income,
                commission_type=order.commission_type, commission_rate=order.commission_rate,
                commission_fixed=order.commission_fixed, game_commission_snapshot=order.game_commission_snapshot)]
        active = {r.provider_account_id:r for r in assignments if not r.released_at}
        if lifecycle_cycles is not None and any(
                row.provider_account_id in lifecycle_cycles
                and row.provider_business_cycle != lifecycle_cycles[row.provider_account_id]
                for row in assignments):
            raise IllegalTransitionError('改派陪玩原席位来源周期待核对')
        if (not active or len(active) != required_team_size(order) or
                any(not row.provider_account_id or row.settled_at for row in assignments)):
            raise IllegalTransitionError('订单陪玩记录不完整或存在历史结算，请先核对')
        if sum(row.settlement_base for row in active.values()) != order.amount:
            raise IllegalTransitionError('订单份额与原付款不一致，请先核对')
        if any(old not in active for old, _ in commands) or any(new in active for _, new in commands):
            raise IllegalTransitionError('订单陪玩已变化或接替陪玩已在本单，请刷新后核对')
        # The unique-per-order historical assignment can only be reused when
        # it contains no retained income, settlement or report from prior work.
        for row in assignments:
            if row.provider_account_id in new_ids and (not row.released_at or row.settlement_base or row.provider_income):
                raise IllegalTransitionError('接替陪玩存在历史履约份额，请先核对')
        from wallet.models import ProviderReport
        accounts = {account.pk: account for account in ClubAccount.objects.select_for_update().filter(
            pk__in=new_ids, account_type=ClubAccount.AccountType.PROVIDER).order_by('pk')}
        if len(accounts) != len(new_ids):
            raise IllegalTransitionError('接替陪玩不存在')
        all_ids = sorted(set(new_ids) | set(active))
        profiles = {p.account_id:p for p in EscortProfile.objects.select_for_update().select_related('user','account','level')
                    .filter(account_id__in=all_ids).order_by('account_id')}
        if any(pk not in profiles for pk in new_ids):
            raise IllegalTransitionError('接替陪玩资料不存在')
        from django.db.models import Q
        changed_ids = {old for old, _ in commands} | set(new_ids)
        changed_user_ids = {profiles[pk].user_id for pk in changed_ids if pk in profiles}
        changed_user_ids.update(row.provider_id for row in assignments if row.provider_account_id in changed_ids and row.provider_id)
        if ProviderReport.objects.filter(order=order).filter(Q(provider_account_id__in=changed_ids) |
                Q(provider_account__isnull=True, provider_id__in=changed_user_ids)).exists():
            raise IllegalTransitionError('所选陪玩已有履约报单，请先核对')
        from .commission import resolve
        from .pricing import PricingError, resolve_active_promotion
        from .models import GameCategory, GameCommissionProfile, OrderPlatformSubsidy, OrderPlatformSubsidyShare
        from .settlement import compute_provider_income, aggregate_order_split
        service, legacy_minimum_review = _purchased_service(order, catalog_service,
            legacy_review=data.get('legacy_minimum_review'), operator=operator)
        # Admission must use the same purchased scope as commission matching;
        # do not mutate the persisted order or its original service reference.
        admission = copy(order)
        admission._state = copy(order._state)
        admission._state.fields_cache = {**order._state.fields_cache, 'service': service}
        # A service skill is tied to a mutable catalog row. If that row has
        # moved to a different scope, its id alone cannot prove that the
        # replacement acquired the original skill before or after the move.
        skill_fields = ('game_category_id', 'provider_kind', 'platform', 'security_level',
                        'provider_grade', 'gameplay_id', 'catalog_family')
        scope_changed = any(getattr(service, key) != getattr(catalog_service, key) for key in skill_fields)
        locked_game_profile = None
        if service.game_category_id:
            try:
                GameCategory.objects.select_for_update().get(pk=service.game_category_id)
            except GameCategory.DoesNotExist as exc:
                raise IllegalTransitionError('原订单游戏分类不存在，请先核对') from exc
            locked_game_profile = GameCommissionProfile.objects.select_for_update().filter(
                game_category_id=service.game_category_id).first()
        if service.service_category_id:
            from .models import ServiceCategory
            if not ServiceCategory.objects.filter(pk=service.service_category_id).exists():
                raise IllegalTransitionError('原订单服务分类不存在，请先核对')
        booking = Reservation.objects.filter(order=order, status=Reservation.Status.CONVERTED).first()
        from wallet.refund_recovery_order import enabled as recovery_enabled
        from wallet.models import RefundDepositDeficit
        for account_id in new_ids:
            profile = profiles[account_id]
            reject = profile.order_acceptance_error(admission, provider_gender=order.provider_gender,
                                                    allow_refund_recovery=True,
                                                    require_current_schedule=not bool(booking))
            if reject:
                raise IllegalTransitionError(reject)
            if scope_changed and service.game_category_id:
                from users.qualifications import service_skill
                if not service_skill(profile, admission, exclude_service_ids={catalog_service.pk}):
                    raise IllegalTransitionError('商品服务范围已变化，接替陪玩缺少可核实的原服务技能')
            if booking:
                from users.models import EscortSchedule
                if not EscortSchedule.provider_covers_window(profile.user, booking.start_time, booking.end_time, lock=True):
                    raise IllegalTransitionError('接替陪玩设置的接单档期未覆盖原预约时段')
                # Profiles are already locked in account order. Reuse the
                # booking writer's current-read conflict checks so a single
                # reservation or converted order committed while we waited
                # cannot be hidden by an earlier repeatable-read snapshot.
                from .reservation_team import _check_slot
                from .reservation_services import ReservationConflictError
                try:
                    _check_slot(accounts[account_id], booking.start_time, booking.end_time, exclude_id=booking.pk)
                except ReservationConflictError as exc:
                    raise IllegalTransitionError('接替陪玩在原预约时段已有其他预约') from exc
            # A future appointment normally does not consume an active slot,
            # but the refund-gap exception may only be used with a currently
            # available ordinary capacity slot.
            gap_exception = (recovery_enabled() and RefundDepositDeficit.objects.filter(
                profile=profile, account_id=account_id, remaining__gt=0).exists())
            if not booking or gap_exception:
                from .capacity import current_capacity_limit
                from .services import count_capacity_orders
                if count_capacity_orders(account_id=account_id, exclude_order_ids=[order.pk]) >= current_capacity_limit():
                    raise IllegalTransitionError('接替陪玩接单名额已满')
        subsidy = OrderPlatformSubsidy.objects.select_for_update().filter(order=order).first()
        if subsidy and required_team_size(order) != 1:
            raise IllegalTransitionError('多人补贴订单的分摊基数需先核对')
        from .transfer_limit import record_locked
        record_locked(order, commands)
        from wallet.refund_recovery_order import sample_unstarted_clock
        handoff_clock = sample_unstarted_clock(order, account_ids=[old for old, _ in commands])
        now = handoff_clock.at if handoff_clock is not None else timezone.now()
        changes = []
        notice_handoffs = []
        from site_messages.prestart_handoff_notice import enabled as handoff_notice_enabled
        for old_id, new_id in commands:
            old = active[old_id]
            if old.settlement_base < old.provider_income:
                raise IllegalTransitionError('原陪玩结算份额无效，请先核对')
            try:
                from .models import OrderPointsRedemption
                points_receipt = OrderPointsRedemption.objects.filter(order=order).first() if points_order else None
                # A later sales promotion does not change an already paid
                # game order or prevent resolving its incoming game rate.
                promotion = None if order.game_commission_snapshot else resolve_active_promotion(service)
                if order.game_commission_snapshot:
                    # Reuse the current locking read captured above. A plain
                    # query here could reuse a snapshot from before a racing
                    # store configuration change committed.
                    from .game_commission import payment_snapshot
                    from .commission import metadata
                    evidence = payment_snapshot(service, profile=locked_game_profile, existing_order=True,
                        provider=profiles[new_id],
                        legacy_service_rate=not bool(order.game_commission_snapshot.get('personal_rules')))
                    if not evidence:
                        raise ValueError('原订单游戏抽成范围待核对')
                    plan = metadata('PERCENT', evidence['rate'], 0, 'game_commission', locked=True)
                    plan['game_commission_snapshot'] = evidence
                else:
                    plan = resolve(service, subsidy.basis if subsidy else points_receipt.deal_units if points_receipt else order.amount,
                                   profile=profiles[new_id], promotion=promotion,
                                   existing_game_order=bool(order.game_commission_snapshot))
            except (PricingError, ValueError) as exc:
                raise IllegalTransitionError(str(exc)) from exc
            if order.game_commission_snapshot and not plan.get('game_commission_snapshot'):
                raise IllegalTransitionError('原订单游戏抽成范围待核对，不能使用其他抽成规则改派')
            old_snapshot = _snapshot(old)
            if handoff_notice_enabled():
                old_snapshot['provider_business_cycle'] = old.provider_business_cycle
            if plan['commission_type'] == 'FIXED' and required_team_size(order) > 1 and order.amount:
                plan['commission_fixed'] = _fixed_for_assignment(order, active, old, plan['commission_fixed'])
            elif points_order and plan['commission_type'] == 'FIXED':
                plan['commission_fixed'] = min(plan['commission_fixed'], order.amount)
            income = compute_provider_income(old.settlement_base, plan['commission_type'], plan['commission_rate'], plan['commission_fixed'])
            base = old.settlement_base
            old.settlement_base = old.provider_income = old.commission_fixed = 0
            old.released_at = now
            old.save(update_fields=['settlement_base','provider_income','commission_fixed','released_at'])
            incoming_defaults = {
                'provider':profiles[new_id].user, 'provider_name_snapshot':profiles[new_id].display_name,
                'provider_business_cycle': (lifecycle_cycles[new_id] if lifecycle_cycles is not None
                                            else accounts[new_id].business_cycle),
                'settlement_base':base, 'provider_income':income, 'commission_type':plan['commission_type'],
                'commission_rate':plan['commission_rate'], 'commission_fixed':plan['commission_fixed'],
                'game_commission_snapshot':plan.get('game_commission_snapshot', {}), 'released_at':None, 'settled_at':None}
            incoming, _ = OrderProvider.objects.update_or_create(order=order, provider_account_id=new_id,
                                                                  defaults=incoming_defaults)
            if subsidy:
                old_share = OrderPlatformSubsidyShare.objects.select_for_update().filter(subsidy=subsidy, assignment=old).first()
                if not old_share or old_share.paid_at or old_share.retained_amount:
                    raise IllegalTransitionError('平台补贴份额不完整或已结算，请先核对')
                old_snapshot['subsidy_amount'] = old_share.amount
                funded = compute_provider_income(subsidy.basis, plan['commission_type'], plan['commission_rate'], plan['commission_fixed'])
                old_share.amount = 0; old_share.save(update_fields=['amount'])
                target, _ = OrderPlatformSubsidyShare.objects.get_or_create(subsidy=subsidy, assignment=incoming,
                                                                            defaults={'amount': 0})
                if target.paid_at or target.retained_amount or target.amount:
                    raise IllegalTransitionError('接替陪玩存在历史补贴，请先核对')
                target.amount = funded; target.save(update_fields=['amount'])
            new_snapshot = _snapshot(incoming)
            if handoff_notice_enabled():
                new_snapshot['provider_business_cycle'] = incoming.provider_business_cycle
            if legacy_minimum_review is not None:
                new_snapshot['legacy_minimum_review'] = legacy_minimum_review
            if subsidy:
                new_snapshot['subsidy_amount'] = funded
            handoff = OrderPrestartHandoff.objects.create(order=order, old_provider_id=old_id, new_provider_id=new_id,
                operator=operator, old_snapshot=old_snapshot, new_snapshot=new_snapshot, reason=reason)
            notice_handoffs.append(handoff)
            from .original_attribution import prestart_reassigned
            prestart_reassigned(order, old_id, new_id, at=now, commission_plan=plan)
            active.pop(old_id); active[new_id] = incoming
            changes.append((old_id, new_id))
        primary = next((new for old, new in commands if old == order.provider_account_id), order.provider_account_id)
        if primary not in active:
            raise IllegalTransitionError('订单主陪玩无法核对')
        current = active[primary]
        order.provider, order.provider_account_id = current.provider, primary
        order.provider_name_snapshot = current.provider_name_snapshot
        if required_team_size(order) == 1:
            order.commission_rate = current.commission_rate
            order.commission_type = current.commission_type
            order.commission_fixed = current.commission_fixed
            order.commission_source = plan['commission_source']
        split = aggregate_order_split(order.amount, list(active.values()),
            order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0)
        order.provider_income, order.inviter_commission, order.shop_income = split.provider_income, split.inviter_commission, split.shop_income
        order.save(update_fields=['provider','provider_account','provider_name_snapshot','commission_rate','commission_type',
                                  'commission_fixed','commission_source','provider_income','inviter_commission','shop_income'])
        if points_order:
            try:
                funding_plan = plan_verified_order(order.pk)
                from .addon_funding_release import record_prestart_funding_release
                from rest_framework.exceptions import ValidationError
                record_prestart_funding_release(order, handoff, funding_plan)
                verify_order_funding(order.pk)
            except (PointsFundingPlanError, ValidationError) as exc:
                raise IllegalTransitionError('积分订单改派后的收益或门店补资待核对') from exc
        from wallet.refund_recovery_order import cancel_unstarted, reserve_many
        cancel_unstarted(
            order, account_ids=[old for old, _ in changes],
            reason='TRANSFERRED_UNSTARTED', now=handoff_clock,
        )
        if recovery_enabled():
            try:
                reserve_many([profiles[new] for _, new in changes], order)
            except ValueError as exc:
                raise IllegalTransitionError(str(exc)) from exc
        log_only(order, action=OrderStatusLog.Action.TRANSFER, operator=operator_user, operator_account=operator,
                 reason=('服务前更换陪玩：' + ', '.join(f'{a}→{b}' for a,b in changes) + '；' + reason)[:255])
        if handoff_notice_enabled():
            from site_messages.prestart_handoff_notice import record_all
            status_log = order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER,
                operator_account=operator).order_by('-pk').first()
            record_all(order, status_log, notice_handoffs)
        refresh_escort_status(account_ids=all_ids)
        from .notifier import notify_order_update
        transaction.on_commit(lambda: notify_order_update(order))
        return order, changes
