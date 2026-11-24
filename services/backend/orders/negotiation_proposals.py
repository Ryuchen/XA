"""Proposal and decision records only; no refund or settlement is executed."""
from collections.abc import Mapping
import hashlib
import json
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from club_accounts.models import ClubAccount
from console.permissions import get_account_permissions, is_console_account
from .points_business_access import scoped_refund_case_operation
from .models import AfterSale, NegotiationDecision, NegotiationProposal, Order
from .notifier import notify_order_update


def enabled():
    return bool(getattr(settings, 'NEGOTIATION_PROPOSAL_ENABLED', False))


def _lock_case(case_id):
    order_id = AfterSale.objects.filter(pk=case_id).values_list('order_id', flat=True).first()
    if order_id is None:
        raise ValidationError('售后记录不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    case = AfterSale.objects.select_for_update().get(pk=case_id, order=order)
    return order, case


def _scope(order, case, *, zero=False):
    if case.addon_payment_id is not None:
        raise ValidationError('该售后仅适用于指定加购，不能修改主单协商方案')
    from .points_checkout_test import ordinary_production_enabled
    if ordinary_production_enabled():
        from .models import OrderAddonRefund
        if OrderAddonRefund.objects.filter(order=order, proposal__case=case,
                proposal__case_opened_at=case.created_at, proposal__case_request_id=case.client_request_id).exists():
            raise ValidationError('加购退款售后已确认，请另行发起主单协商')
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from .points_negotiation_preview import agreement_enabled, preview_locked
        if not agreement_enabled() or not case.client_request_id:
            raise ValidationError('积分订单的协商方案尚未接通或售后标识待核对')
        if zero:
            from .points_transfer_zero_followup import applies as transfer_zero_applies, prepare_locked as transfer_zero_prepare
            if transfer_zero_applies(order):
                return [transfer_zero_prepare(order, case)[4]['account_id']]
            from .points_zero_followup import applies as points_zero_applies, prepare_locked as prepare_points_zero
            if points_zero_applies(order):
                return [prepare_points_zero(order, case)[4]['account_id']]
        from .models import OrderOriginalShareTransfer
        from .points_business_access import permits as points_permits
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if transferred and order.refund_records.exists():
            from .points_transfer_followup_preview import internal_agreement_enabled
            if not internal_agreement_enabled():
                raise ValidationError('积分转单后续协商确认尚未开放')
            if order.refund_records.count() >= 3:
                from .points_round_refund_preview import preview_locked as transfer_followup_preview
            elif order.refund_records.count() == 2:
                from .points_third_refund_preview import preview_locked as transfer_followup_preview
            else:
                from .points_transfer_followup_preview import preview_locked as transfer_followup_preview
            preview = transfer_followup_preview(case.pk, refund_equivalent_units=10)
        elif transferred:
            from .points_transfer_refund_preview import agreement_available_for_order as transfer_agreement_available
            from .points_transfer_refund_preview import preview_locked as transfer_preview
            if not transfer_agreement_available(order) or order.refund_records.exists():
                raise ValidationError('积分转单协商确认尚未开放')
            preview = transfer_preview(case.pk, refund_equivalent_units=10)
        elif order.refund_records.exists() and (
                getattr(settings, 'SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING', False)
                or any(points_permits(order, phase='refund', kind=kind) for kind in ('service', 'addon'))):
            from .points_followup_preview import preview_followup
            preview = preview_followup(case.pk, refund_equivalent_units=10)
            if preview.get('has_addons'):
                from .models import PointsNegotiationPosting
                previous = PointsNegotiationPosting.objects.get(points_refund_id=preview['prior_refund_id'])
                from .points_followup_termination import addon_followup_available
                if not addon_followup_available(order, previous, prior_paid=preview['prior_paid']):
                    raise ValidationError('带加购的后续退款目前仅供资金预览')
        else:
            preview = preview_locked(case.pk, refund_equivalent_units=10)
        return [item['account_id'] for item in preview['provider_terms']]
    from .ordinary_transfer_followup import applies as transfer_followup_applies, calculation_locked as transfer_followup_calculation
    if transfer_followup_applies(order):
        return [item['account_id'] for item in transfer_followup_calculation(order, case, 0)['provider_terms']]
    from .negotiation_transfer_preview import agreement_testing_available, preview_locked as ordinary_transfer_preview
    if agreement_testing_available(order):
        return [item['account_id'] for item in ordinary_transfer_preview(case.pk, 10)['provider_terms']]
    from .negotiation_zero import marked_execution_id
    zero_followup = marked_execution_id(order) is not None
    if zero_followup or order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED):
        from .ordinary_addon_followup_preview import (applies as addon_followup,
            agreement_testing_available, preview_case_locked)
        if addon_followup(order):
            if not agreement_testing_available(order):
                raise ValidationError('带加购的追加协商暂仅支持资金预览，确认及退款尚未开放')
            from .settlement import SettlementError
            try:
                return [preview_case_locked(case.pk, refund_units=0).account_id]
            except SettlementError as exc:
                raise ValidationError(str(exc)) from exc
        from .negotiation_followup import _preview_followup_locked, followup_enabled
        if not followup_enabled():
            raise ValidationError('零退款协商终止后的新售后方案尚未接通，请联系客服核对' if zero_followup
                                  else '退款后的新售后方案尚未接通，请联系客服核对')
        return [term['account_id'] for term in _preview_followup_locked(case.pk, refund_units=0)['provider_terms']]
    scope = case.affected_provider_account_ids
    continued_case = (order.status == Order.Status.COMPLETED and order.completed_at is not None
                      and case.created_at is not None and case.created_at <= order.completed_at)
    from .subsidy_agreement import applies as is_subsidy, enabled as subsidy_enabled
    subsidy = is_subsidy(order)
    if subsidy and order.status == Order.Status.COMPLETED and order.completed_at and case.created_at:
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        continued_case = bool(deadline and case.created_at < deadline)
    if (case.status != 'OPEN' or (order.status != Order.Status.IN_SERVICE and not continued_case)
            or order.payment_status != Order.PaymentStatus.PAID
            or order.is_gift_snapshot or not order.customer_account_id or case.account_id != order.customer_account_id
            or not case.client_request_id
            or not isinstance(scope, list) or not scope or any(type(x) is not int or x <= 0 for x in scope)
            or len(scope) != len(set(scope))):
        raise ValidationError('订单或售后状态已变化，请刷新后核对')
    if subsidy and not subsidy_enabled():
        raise ValidationError('安全箱补贴协商尚未开放')
    candidates = order.providers.all() if subsidy else order.providers.filter(released_at__isnull=True)
    active = list(candidates.values_list('provider_id', 'provider_account_id'))
    if not active and order.provider_id:
        active = [(order.provider_id, order.provider_account_id)]
    if not active or any(not user or not account for user, account in active):
        raise ValidationError('当前陪玩身份待核对')
    from users.models import EscortProfile
    profiles = set(EscortProfile.objects.select_for_update().filter(
        account_id__in=[account for _, account in active]).values_list('user_id', 'account_id'))
    if profiles != set(active) or not set(scope).issubset({account for _, account in active}):
        raise ValidationError('当前履约陪玩已变化，请重新核对方案')
    if order.customer_account_id in scope:
        raise ValidationError('订单参与人身份异常，请联系管理员核对')
    return sorted(scope)


def _require_followup_zero_enabled(order, refund_units):
    """Do not obtain consent for a follow-up that cannot be executed."""
    if refund_units:
        return
    from .points_service_payment import needs_integrated_processing
    from .models import OrderOriginalShareTransfer
    if (needs_integrated_processing(order) and order.refund_records.exists()
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        from .points_transfer_refund_access import permits
        from .points_transfer_zero_followup import applies
        if applies(order) and permits(order, phase='refund'):
            return
        raise ValidationError('积分转单后的零退款协商终止尚未接通')
    from .points_zero_followup import applies as points_zero_applies
    from .points_business_access import permits as points_permits
    if points_zero_applies(order) and any(points_permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
        return
    from .negotiation_followup import followup_enabled
    from .negotiation_zero import marked_execution_id
    if (followup_enabled() and (order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
                                or marked_execution_id(order) is not None)
            and not getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False)):
        raise ValidationError('零退款追加协商尚未开放')


def _terms(value, scope):
    if not isinstance(value, list) or len(value) != len(scope):
        raise ValidationError('请为每位受影响陪玩填写保留收益')
    result = []
    for item in value:
        if (not isinstance(item, Mapping) or set(item) != {'account_id', 'retained_units'}
                or type(item['account_id']) is not int or type(item['retained_units']) is not int
                or not 0 <= item['retained_units'] <= 2147483647):
            raise ValidationError('陪玩保留收益无效')
        result.append({'account_id': item['account_id'], 'retained_units': item['retained_units']})
    result.sort(key=lambda item: item['account_id'])
    if [item['account_id'] for item in result] != scope:
        raise ValidationError('陪玩收益与受影响范围不一致')
    return result


def _snapshot_terms(value, scope):
    keys = {'account_id', 'retained_units', 'original_income_units', 'burden_units', 'additional_units'}
    if (not isinstance(value, list) or len(value) != len(scope) or any(
            not isinstance(item, dict) or set(item) != keys or any(type(item[k]) is not int or item[k] < 0 for k in keys)
            or item['retained_units'] != max(item['original_income_units'] - item['burden_units'], 0)
            or item['additional_units'] != max(item['burden_units'] - item['original_income_units'], 0) for item in value)
            or [item['account_id'] for item in value] != scope):
        raise ValidationError('方案中的陪玩收益依据不完整，请联系客户服务核对')
    return value


def _staff(account):
    return bool(account and account.account_type == ClubAccount.AccountType.STAFF
                and is_console_account(account) and 'support:edit' in get_account_permissions(account))


def _episode(case, proposal):
    return (proposal.case_opened_at == case.created_at and proposal.case_request_id == case.client_request_id)


def _digest(case, version, scope, refund_units, terms, explanation, segments=None, ordinary_segment_plan=None,
            ordinary_paid_snapshot=None):
    payload = {'case':case.pk, 'opened_at':case.created_at.isoformat(), 'case_request_id':case.client_request_id,
               'version':version, 'scope':scope, 'refund_units':refund_units,
               'provider_terms':terms, 'explanation':explanation, 'affected_segment_ids':segments}
    if ordinary_segment_plan is not None:
        payload['ordinary_segment_pending_plan'] = ordinary_segment_plan
    if ordinary_paid_snapshot is not None:
        payload['ordinary_paid_settlement_snapshot'] = ordinary_paid_snapshot
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _verified(case, proposal):
    scope = proposal.affected_accounts
    if (not isinstance(scope, list) or not scope or any(type(item) is not int or item <= 0 for item in scope)
            or scope != sorted(set(scope)) or _snapshot_terms(proposal.provider_terms, scope) != proposal.provider_terms
            or type(proposal.refund_units) is not int or proposal.refund_units < 0 or proposal.refund_units % 10
            or not _episode(case, proposal) or proposal.terms_hash != _digest(case, proposal.version,
            proposal.affected_accounts, proposal.refund_units, proposal.provider_terms, proposal.explanation,
            proposal.affected_segment_ids, proposal.ordinary_segment_pending_plan,
            proposal.ordinary_paid_settlement_snapshot)):
        raise ValidationError('方案内容或所属售后已变化，请联系客户服务核对')
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(case.order):
        if proposal.refund_units == 0:
            from .points_zero_followup import verify_proposal
            verify_proposal(case, proposal)
        else:
            from .points_negotiation_preview import verified_asset_terms
            verified_asset_terms(proposal)


def _decisions(case, proposal, *, lock=False):
    query = proposal.decisions.order_by('pk')
    if lock:
        query = query.select_for_update()
    decisions = list(query)
    expected = {(case.order.customer_account_id, 'BOSS'), *((account, 'PROVIDER') for account in proposal.affected_accounts)}
    if {(row.account_id, row.role) for row in decisions} != expected or len(decisions) != len(expected):
        raise ValidationError('方案确认人记录不完整，请联系管理员')
    states = [row.accepted for row in decisions]
    if (any((row.accepted is None) != (row.decided_at is None) for row in decisions)
            or (proposal.status == 'ACCEPTED' and (not all(state is True for state in states)
                or proposal.accepted_at != max(row.decided_at for row in decisions)))
            or (proposal.status != 'ACCEPTED' and proposal.accepted_at is not None)
            or (proposal.status == 'PENDING' and (any(state is False for state in states) or all(state is True for state in states)))
            or (proposal.status == 'REJECTED' and not any(state is False for state in states))):
        raise ValidationError('方案确认状态不一致，请联系管理员')
    return decisions


def serialize(proposal, *, actor, staff=False):
    _verified(proposal.case, proposal)
    decisions = _decisions(proposal.case, proposal)
    mine = next((row for row in decisions if row.account_id == actor.pk), None)
    if not staff and (not mine or actor.account_type != mine.role or not actor.is_active or not actor.can_login):
        raise ValidationError('无权查看此方案')
    if staff or mine.role == 'BOSS':
        terms = proposal.provider_terms
        visible_decisions = decisions
    else:
        terms = [item for item in proposal.provider_terms if item['account_id'] == actor.pk]
        visible_decisions = [mine]
    from .points_service_payment import needs_integrated_processing
    from .subsidy_agreement import applies as is_subsidy
    subsidy = is_subsidy(proposal.case.order)
    transfer_detail = None
    if subsidy:
        from .subsidy_transfer_agreement import applies as transfer_subsidy, enabled as transfer_agreement_enabled, display_terms
        if transfer_agreement_enabled() and transfer_subsidy(proposal.case.order):
            transfer_detail = display_terms(proposal)
    points_values = None
    from .ordinary_transfer_addon_agreement import display as display_addons
    addon_terms = display_addons(proposal, account_id=None if staff or mine.role == 'BOSS' else actor.pk, staff=staff)
    if needs_integrated_processing(proposal.case.order):
        if proposal.refund_units == 0:
            from .points_zero_followup import verify_proposal
            verify_proposal(proposal.case, proposal)
        else:
            from .points_negotiation_preview import verified_asset_terms
            points_values = verified_asset_terms(proposal)
    from .points_negotiation_termination import available_for_order as points_termination_available
    points_execution_available = points_termination_available(proposal.case.order)
    if points_values is not None and points_values.get('prior_refund_id'):
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=proposal.case.order).exists():
            if proposal.case.order.refund_records.count() >= 3:
                from .points_round_refund_preview import available_for_case
                points_execution_available = available_for_case(proposal.case, refund_units=proposal.refund_units)
            elif proposal.case.order.refund_records.count() == 2:
                from .points_third_refund_preview import available_for_case
                points_execution_available = available_for_case(proposal.case, refund_units=proposal.refund_units)
            else:
                from .points_transfer_followup_termination import available_for_case
                points_execution_available = available_for_case(proposal.case)
        else:
            from .points_followup_termination import endpoint_available
            points_execution_available = endpoint_available(points_values['prior_refund_id'])
    segment_ids = proposal.affected_segment_ids
    if segment_ids and not staff and mine.role != 'BOSS':
        from .models import OrderParticipationSegment
        segment_ids = list(OrderParticipationSegment.objects.filter(pk__in=segment_ids,
            attribution__order=proposal.case.order, provider_account_id=actor.pk).order_by('pk').values_list('pk', flat=True))
    from .models import OrdinaryTransferFollowupRound
    ordinary_round = OrdinaryTransferFollowupRound.objects.filter(proposal=proposal).first()
    ordinary_segment_plan = proposal.ordinary_segment_pending_plan
    if ordinary_round:
        from .ordinary_transfer_followup import _hash
        if ordinary_round.facts_hash != _hash(proposal.pk, ordinary_round.facts):
            raise ValidationError('追加协商逐段承担凭证异常')
        frozen_plan = ordinary_round.facts.get('segment_pending_plan')
        if not isinstance(frozen_plan, list) or frozen_plan != ordinary_segment_plan:
            raise ValidationError('追加协商逐段承担计划缺失')
    if ordinary_segment_plan is not None and not staff and mine.role != 'BOSS':
        ordinary_segment_plan = [item for item in ordinary_segment_plan if item['account_id'] == actor.pk]
    return {'id': proposal.pk, 'version':proposal.version, 'status':proposal.status,
            'refund_units':proposal.refund_units, 'provider_terms':terms, 'explanation':proposal.explanation,
            'affected_segment_ids': segment_ids,
            **({'segment_pending_plan': ordinary_segment_plan} if ordinary_segment_plan is not None else {}),
            **({'refund_coin_units': 0, 'refund_points_hundredths': 0,
                'points_execution_available': points_execution_available}
               if needs_integrated_processing(proposal.case.order) and proposal.refund_units == 0 else {}),
            **({'calculation_kind':'PLATFORM_SUBSIDY'} if subsidy else {}),
            **({'segment_evidence':transfer_detail} if transfer_detail is not None else {}),
            **({'addon_responsibilities': addon_terms} if addon_terms is not None else {}),
            **({'refund_coin_units': points_values['coin_units'], 'refund_points_hundredths': points_values['points_hundredths'],
                'refund_is_final': points_values['is_final'], 'points_execution_available':
                    points_execution_available} if points_values is not None else {}),
            **({'segment_pending_plan': [item for item in points_values['segment_pending_plan']
                    if staff or mine.role == 'BOSS' or item['account_id'] == actor.pk]}
               if points_values is not None and points_values.get('segment_pending_plan') is not None else {}),
            **({'refund_coin_sources': {'prepaid_units': points_values['prepaid_units'], 'gift_units': points_values['gift_units'],
                                         'compensation_units': points_values['compensation_units']}}
               if points_values is not None and (staff or mine.role == 'BOSS') else {}),
            **({'rounding_to_store_units':0 if subsidy else sum(item['burden_units'] for item in proposal.provider_terms)-proposal.refund_units}
               if staff or mine.role == 'BOSS' else {}),
            'created_at':proposal.created_at, 'accepted_at':proposal.accepted_at,
            'my_decision':None if staff else mine.accepted,
            **({'request_id':proposal.request_id, 'created_by_id':proposal.created_by_id} if staff else {}),
            'decisions':[{'account_id':row.account_id, 'role':row.role, 'accepted':row.accepted,
                          'decided_at':row.decided_at} for row in visible_decisions]}


def latest_for_case(case):
    latest = NegotiationProposal.objects.filter(case=case).order_by('-version').first()
    return (latest.version if latest else 0, latest if latest and _episode(case, latest) else None)


def assert_case_can_resolve(case):
    """Do not close away an outstanding or already agreed proposal.

    The caller holds the order and case locks. This guard remains active if
    the feature is subsequently switched off. Older stores without migration
    0119 have never recorded these proposals and may resolve ordinary cases.
    """
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        raise RuntimeError('售后收口核对必须在门店事务内执行')
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    required = {NegotiationProposal._meta.db_table, NegotiationDecision._meta.db_table}
    if not required.issubset(tables):
        if enabled() or tables.intersection(required):
            raise ValidationError('协商记录表不完整，请联系管理员核对')
        return
    proposals = list(NegotiationProposal.objects.select_for_update().filter(case=case,
        case_opened_at=case.created_at, case_request_id=case.client_request_id).order_by('version'))
    from .models import NegotiationExecution, PointsNegotiationPosting, PointsZeroFollowupTermination
    for index, proposal in enumerate(proposals):
        _verified(case, proposal)
        decisions = _decisions(case, proposal, lock=True)
        if index + 1 < len(proposals):
            later = proposals[index + 1]
            if (later.version != proposal.version + 1 or later.created_at <= proposal.created_at
                    or proposal.status == 'PENDING'
                    or any(row.decided_at and row.decided_at >= later.created_at for row in decisions)
                    or NegotiationExecution.objects.filter(proposal=proposal).exists()
                    or PointsNegotiationPosting.objects.filter(proposal=proposal).exists()
                    or PointsZeroFollowupTermination.objects.filter(proposal=proposal).exists()):
                raise ValidationError('先前协商方案的确认或执行顺序待核对')
    latest = proposals[-1] if proposals else None
    if latest and latest.status == 'ACCEPTED':
        raise ValidationError('已有各方同意的协商方案，请先完成约定处理，不能直接结束售后')
    if latest and latest.status == 'PENDING':
        raise ValidationError('协商方案仍在等待当事人确认，请先处理该方案')
    if latest and latest.status != 'REJECTED':
        raise ValidationError('最新协商方案状态待核对')


def _proposal_calculation(order, case, scope, refund_units, requested_terms=None):
    from .ordinary_transfer_followup import applies as transfer_followup_applies, calculation_locked as transfer_followup_calculation
    if transfer_followup_applies(order):
        if refund_units == 0 and not getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False):
            raise ValidationError('转单后零退款追加协商结案尚未开放')
        calculation = transfer_followup_calculation(order, case, refund_units)
        if [item['account_id'] for item in calculation['provider_terms']] != scope:
            raise ValidationError('追加协商受影响陪玩已变化')
        return calculation, None
    from .ordinary_addon_followup_preview import (agreement_testing_available, preview_case_locked, provider_terms)
    if agreement_testing_available(order):
        from .settlement import SettlementError
        from .ordinary_addon_followup_termination import zero_enabled
        if not refund_units and not zero_enabled():
            raise ValidationError('带加购的零退款追加协商尚未开放')
        try:
            estimate = preview_case_locked(case.pk, refund_units=refund_units)
        except SettlementError as exc:
            raise ValidationError(str(exc)) from exc
        if scope != [estimate.account_id]:
            raise ValidationError('追加售后受影响陪玩已变化')
        return {'provider_terms': provider_terms(estimate)}, None
    from .subsidy_agreement import applies as is_subsidy, calculate as subsidy_calculation
    if is_subsidy(order):
        from .subsidy_transfer_agreement import applies as transfer_subsidy, calculate as transfer_calculation
        if transfer_subsidy(order):
            return transfer_calculation(order, case, scope, refund_units, requested_terms or []), None
        return subsidy_calculation(order, case, scope, refund_units, requested_terms or []), None
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from .points_negotiation_preview import agreement_enabled, asset_terms, preview_locked
        if not agreement_enabled():
            raise ValidationError('积分订单协商方案尚未开放或退款等值无效')
        if not refund_units:
            from .points_zero_followup import prepare_locked
            _, _, _, _, term = prepare_locked(order, case)
            if scope != [term['account_id']]:
                raise ValidationError('继续履约陪玩已变化')
            return {'provider_terms': [term]}, None
        from .models import OrderOriginalShareTransfer
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if transferred and order.refund_records.exists():
            from .points_transfer_followup_preview import internal_agreement_enabled
            if not internal_agreement_enabled():
                raise ValidationError('积分转单后续协商确认尚未开放')
            if order.refund_records.count() >= 3:
                from .points_round_refund_preview import preview_locked as transfer_followup_preview
            elif order.refund_records.count() == 2:
                from .points_third_refund_preview import preview_locked as transfer_followup_preview
            else:
                from .points_transfer_followup_preview import preview_locked as transfer_followup_preview
            calculation = transfer_followup_preview(case.pk, refund_equivalent_units=refund_units)
            if order.refund_records.count() >= 2 and calculation['shortfall_units']:
                raise ValidationError('陪玩可用收益、钱包及押金不足，暂不能退款')
            if order.refund_records.count() >= 2 and calculation['deposit_units']:
                from wallet.refund_deposit import enabled as deposit_enabled
                from wallet.refund_recovery_order import enabled as recovery_enabled
                if not deposit_enabled() or not recovery_enabled():
                    raise ValidationError('第三轮退款押金扣款尚未启用')
        elif transferred:
            from .points_transfer_refund_preview import agreement_available_for_order as transfer_agreement_available
            from .points_transfer_refund_preview import preview_locked as transfer_preview
            if not transfer_agreement_available(order) or order.refund_records.exists():
                raise ValidationError('积分转单协商确认尚未开放')
            calculation = transfer_preview(case.pk, refund_equivalent_units=refund_units)
        elif order.refund_records.exists():
            from .points_business_access import permits
            formal = any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon'))
            if not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING', False):
                raise ValidationError('积分订单后续协商方案尚未开放')
            from .points_followup_preview import preview_followup
            calculation = preview_followup(case.pk, refund_equivalent_units=refund_units)
            from .points_followup_termination import formal_followup_funding_ready
            if formal and not formal_followup_funding_ready(calculation):
                raise ValidationError('已有陪玩结算的后续退款须先完成资金核对')
            if (not formal and not calculation['prior_paid']
                    and not getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING', False)):
                raise ValidationError('原陪玩未结算的后续协商暂仅支持预览')
        else:
            calculation = preview_locked(case.pk, refund_equivalent_units=refund_units)
        if [row['account_id'] for row in calculation['provider_terms']] != scope:
            raise ValidationError('积分订单受影响陪玩已变化')
        return calculation, asset_terms(calculation)
    from .negotiation_transfer_preview import agreement_testing_available, preview_locked as ordinary_transfer_preview
    if agreement_testing_available(order):
        calculation = ordinary_transfer_preview(case.pk, refund_units)
        if [row['account_id'] for row in calculation['provider_terms']] != scope:
            raise ValidationError('转单受影响陪玩已变化')
        return calculation, None
    if refund_units > order.amount:
        raise ValidationError('退款金额不能超过原店币实付')
    from .negotiation_financial import preview
    return preview(order, scope, refund_units), None


@transaction.atomic
@scoped_refund_case_operation
def create(case_id, *, actor, request_id, expected_version, refund_units, provider_terms, explanation):
    if not enabled() or not _staff(actor):
        raise ValidationError('当前无法登记协商方案')
    if (not isinstance(request_id, str) or not request_id.strip() or not 1 <= len(request_id) <= 64
            or type(expected_version) is not int or not 0 <= expected_version < 2147483647
            or type(refund_units) is not int or not 0 <= refund_units <= 2147483647 or refund_units % 10
            or not isinstance(explanation, str) or not 2 <= len(explanation.strip()) <= 1000):
        raise ValidationError('协商方案格式无效')
    order, case = _lock_case(case_id)
    from django.conf import settings
    if getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
        from .points_service_payment import needs_integrated_processing
        from .models import OrderOriginalShareTransfer
        if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).exists():
            from .financial_lock import lock_order_money
            from .points_receipt_usage import prelock_transfer_refund_receipts
            lock_order_money()
            prelock_transfer_refund_receipts(order)
    if getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False) and order.refund_records.exists():
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from .financial_lock import lock_order_money
            from .points_receipt_usage import prelock_followup_receipts
            lock_order_money()
            prelock_followup_receipts(order)
    from .subsidy_transfer_agreement import applies as transfer_subsidy
    transfer_agreement = transfer_subsidy(order)
    if transfer_agreement:
        from .financial_lock import lock_order_money
        lock_order_money()
    scope = _scope(order, case, zero=refund_units == 0)
    _require_followup_zero_enabled(order, refund_units)
    terms = _terms(provider_terms, scope)
    explanation = explanation.strip()
    calculation, assets = _proposal_calculation(order, case, scope, refund_units, terms)
    verified_terms = calculation['provider_terms']
    from .points_service_payment import needs_integrated_processing
    ordinary_segment_plan = (calculation.get('segment_pending_plan') if not needs_integrated_processing(order) else None)
    ordinary_paid_snapshot = (calculation.get('ordinary_paid_settlement_snapshot')
                              if not needs_integrated_processing(order) else None)
    if terms != [{'account_id':item['account_id'], 'retained_units':item['retained_units']} for item in verified_terms]:
        raise ValidationError('陪玩承担及保留收益已变化，请重新计算方案')
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    previous = NegotiationProposal.objects.select_for_update().filter(case=case, request_id=request_id).first()
    if previous:
        _verified(case, previous)
        from .ordinary_transfer_followup import applies as transfer_followup_applies, verify_draft_locked
        if transfer_followup_applies(order):
            verify_draft_locked(previous, calculation)
        from .ordinary_transfer_addon_agreement import applies as addon_applies, verify_locked as verify_addons
        from .points_service_payment import needs_integrated_processing
        if not needs_integrated_processing(order) and addon_applies(order):
            verify_addons(previous, compare_current=True)
        if (not _episode(case, previous) or previous.created_by_id != actor.pk
                or previous.version != expected_version + 1 or previous.refund_units != refund_units
                or previous.provider_terms != verified_terms or previous.explanation != explanation
                or previous.affected_segment_ids != case.affected_segment_ids
                or previous.ordinary_segment_pending_plan != ordinary_segment_plan
                or previous.ordinary_paid_settlement_snapshot != ordinary_paid_snapshot):
            raise ValidationError('操作凭证已用于其他方案，请刷新核对')
        if assets is not None:
            from .points_negotiation_preview import verified_asset_terms
            if verified_asset_terms(previous) != assets:
                raise ValidationError('积分退款构成已变化，请重新核对方案')
        if not latest or latest.pk != previous.pk:
            raise ValidationError('该方案已被后续方案替换，请刷新核对')
        _decisions(case, previous, lock=True)
        if transfer_agreement:
            from .subsidy_transfer_agreement import display_terms
            display_terms(previous)
        return previous
    if (latest.version if latest else 0) != expected_version:
        raise ValidationError('方案已变化，请刷新后重新核对')
    if latest and _episode(case, latest):
        _verified(case, latest)
        _decisions(case, latest, lock=True)
    if latest and _episode(case, latest) and latest.status == 'PENDING':
        latest.status = 'SUPERSEDED'; latest.save(update_fields=['status'])
    proposal = NegotiationProposal.objects.create(case=case, case_opened_at=case.created_at,
        case_request_id=case.client_request_id, version=expected_version + 1, request_id=request_id,
        created_by=actor, affected_accounts=scope, refund_units=refund_units,
        provider_terms=verified_terms, explanation=explanation, affected_segment_ids=case.affected_segment_ids,
        ordinary_segment_pending_plan=ordinary_segment_plan,
        ordinary_paid_settlement_snapshot=ordinary_paid_snapshot,
        terms_hash=_digest(case, expected_version + 1, scope, refund_units, verified_terms, explanation,
                           case.affected_segment_ids, ordinary_segment_plan, ordinary_paid_snapshot))
    from .ordinary_transfer_addon_agreement import applies as addon_applies, bind_locked as bind_addons
    from .points_service_payment import needs_integrated_processing
    if not needs_integrated_processing(order) and addon_applies(order):
        bind_addons(proposal)
    from .ordinary_transfer_followup import applies as transfer_followup_applies, bind_locked as bind_followup
    if transfer_followup_applies(order):
        bind_followup(proposal, calculation)
    if assets is not None:
        from .models import PointsNegotiationTerms
        from .points_negotiation_preview import asset_digest
        PointsNegotiationTerms.objects.create(proposal=proposal, **assets, evidence_hash=asset_digest(proposal, assets))
    NegotiationDecision.objects.bulk_create([
        NegotiationDecision(proposal=proposal, account_id=order.customer_account_id, role='BOSS'),
        *[NegotiationDecision(proposal=proposal, account_id=account_id, role='PROVIDER') for account_id in scope],
    ])
    if transfer_agreement:
        from .subsidy_transfer_agreement_evidence import bind_draft
        bind_draft(proposal.pk)
    transaction.on_commit(lambda: notify_order_update(order))
    from .negotiation_notices import schedule_proposal_notices
    schedule_proposal_notices(order, scope, 'CREATED', proposal=proposal)
    return proposal


@transaction.atomic
@scoped_refund_case_operation
def decide(case_id, *, actor, proposal_id, accepted):
    if not enabled() or type(proposal_id) is not int or type(accepted) is not bool:
        raise ValidationError('确认请求无效')
    order, case = _lock_case(case_id)
    from django.conf import settings
    if getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
        from .points_service_payment import needs_integrated_processing
        from .models import OrderOriginalShareTransfer
        if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).exists():
            from .financial_lock import lock_order_money
            from .points_receipt_usage import prelock_transfer_refund_receipts
            lock_order_money()
            prelock_transfer_refund_receipts(order)
    if getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False) and order.refund_records.exists():
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from .financial_lock import lock_order_money
            from .points_receipt_usage import prelock_followup_receipts
            lock_order_money()
            prelock_followup_receipts(order)
    from .subsidy_transfer_agreement import applies as transfer_subsidy
    transfer_agreement = transfer_subsidy(order)
    if transfer_agreement:
        from .financial_lock import lock_order_money
        lock_order_money()
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    if not proposal:
        raise ValidationError('方案已变化，请刷新后核对')
    scope = _scope(order, case, zero=proposal.refund_units == 0)
    if (proposal.affected_accounts != scope
            or proposal.affected_segment_ids != case.affected_segment_ids):
        raise ValidationError('方案已变化，请刷新后核对')
    _verified(case, proposal)
    from .ordinary_transfer_addon_agreement import applies as addon_applies, verify_locked as verify_addons
    from .points_service_payment import needs_integrated_processing
    if not needs_integrated_processing(order) and addon_applies(order):
        verify_addons(proposal, compare_current=True)
    _require_followup_zero_enabled(order, proposal.refund_units)
    requested = [{'account_id':item['account_id'], 'retained_units':item['retained_units']}
                 for item in proposal.provider_terms]
    calculation, assets = _proposal_calculation(order, case, scope, proposal.refund_units, requested)
    from .ordinary_transfer_followup import applies as transfer_followup_applies, verify_draft_locked
    if transfer_followup_applies(order):
        verify_draft_locked(proposal, calculation)
    if calculation['provider_terms'] != proposal.provider_terms:
        raise ValidationError('陪玩收益或退款分摊已变化，请联系客服重新确认')
    if not needs_integrated_processing(order) and calculation.get('segment_pending_plan') != proposal.ordinary_segment_pending_plan:
        raise ValidationError('逐段承担计划已变化，请联系客服重新确认')
    if (not needs_integrated_processing(order)
            and calculation.get('ordinary_paid_settlement_snapshot') != proposal.ordinary_paid_settlement_snapshot):
        raise ValidationError('已结算记录已变化，请联系客服重新确认')
    if transfer_agreement:
        from .subsidy_transfer_agreement import display_terms
        display_terms(proposal)
    if assets is not None:
        from .points_negotiation_preview import verified_asset_terms
        if verified_asset_terms(proposal) != assets:
            raise ValidationError('积分退款构成已变化，请联系客服重新确认')
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not latest or latest.pk != proposal.pk:
        raise ValidationError('方案已更新，请刷新后核对')
    decisions = _decisions(case, proposal, lock=True)
    mine = next((row for row in decisions if actor and row.account_id == actor.pk), None)
    if not mine or actor.account_type != mine.role or not actor.is_active or not actor.can_login:
        raise ValidationError('无权确认此方案')
    if mine.accepted is not None:
        if mine.accepted != accepted:
            raise ValidationError('本次确认已记录，如需修改请联系客户服务')
        return proposal
    if proposal.status != 'PENDING':
        raise ValidationError('此方案已结束，请刷新后核对')
    mine.accepted, mine.decided_at = accepted, timezone.now()
    mine.save(update_fields=['accepted', 'decided_at'])
    if not accepted:
        proposal.status = 'REJECTED'
        proposal.save(update_fields=['status'])
    elif all(row.accepted is True for row in decisions):
        proposal.status, proposal.accepted_at = 'ACCEPTED', mine.decided_at
        proposal.save(update_fields=['status', 'accepted_at'])
    transaction.on_commit(lambda: notify_order_update(order))
    if proposal.status in ('ACCEPTED', 'REJECTED'):
        from .negotiation_notices import schedule_proposal_notices
        schedule_proposal_notices(order, scope, proposal.status, proposal=proposal)
    return proposal


class UserNegotiationProposalView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def _visible_case(self, request, order_id):
        case = AfterSale.objects.select_related('order').filter(order_id=order_id, status='OPEN').first()
        account = request.account
        if not case or not account:
            return None
        scope = case.affected_provider_account_ids
        if scope is None:
            _, proposal = latest_for_case(case)
            if proposal:
                scope = proposal.affected_accounts
        allowed = ((account.account_type == ClubAccount.AccountType.BOSS and case.order.customer_account_id == account.pk)
                   or (account.account_type == ClubAccount.AccountType.PROVIDER and isinstance(scope, list)
                       and account.pk in scope))
        return case if allowed else None

    def get(self, request, order_id):
        if not enabled():
            return Response({'code':404, 'msg':'协商方案暂未开放'}, status=404)
        case = self._visible_case(request, order_id)
        if not case:
            return Response({'code':404, 'msg':'协商方案不存在'}, status=404)
        _, proposal = latest_for_case(case)
        if not proposal:
            return Response({'code':0, 'data':None})
        return Response({'code':0, 'data':serialize(proposal, actor=request.account)})

    def post(self, request, order_id):
        if not enabled():
            return Response({'code':404, 'msg':'协商方案暂未开放'}, status=404)
        case = self._visible_case(request, order_id)
        if not case:
            return Response({'code':404, 'msg':'协商方案不存在'}, status=404)
        if not isinstance(request.data, Mapping) or set(request.data) != {'proposal_id', 'accepted'}:
            raise ValidationError('确认请求无效')
        proposal = decide(case.pk, actor=request.account, proposal_id=request.data['proposal_id'], accepted=request.data['accepted'])
        return Response({'code':0, 'data':serialize(proposal, actor=request.account),
                         'msg':'确认已记录，退款仍须由客服处理'})
