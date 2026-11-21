"""Store-local consent for refunds of an individual add-on payment."""
from collections.abc import Mapping
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from stores.db import transaction
from .aftersale_scope import applies_to
from .points_business_access import scoped_addon_refund_action
from .addon_refund_lifecycle import protect_addon_refund
from .models import (AfterSale, Order, OrderAddonPayment, OrderAddonRefundDecision, OrderAddonRefundParticipant,
                     OrderAddonRefundProposal)


def _locked(order_id, payment_id, *, case_id=None):
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('订单不存在')
    cases = AfterSale.objects.select_for_update().filter(order=order, status='OPEN')
    if case_id is not None:
        case = cases.filter(pk=case_id).first()
    else:
        # A different add-on's open case must not capture this payment.
        # When the appropriate case is not unique, the staff member must
        # select it explicitly rather than confirming against the wrong one.
        matches = list(cases.filter(addon_payment_id=payment_id).order_by('pk')[:2])
        if not matches:
            matches = list(cases.filter(addon_payment_id__isnull=True).order_by('pk')[:2])
        if len(matches) > 1:
            raise ValidationError('该加购有多条适用售后，请明确选择售后记录')
        case = matches[0] if matches else None
    payment = (OrderAddonPayment.objects.select_for_update().select_related('intent')
               .filter(pk=payment_id, intent__order=order).first())
    if not payment or not case:
        raise ValidationError('加购或适用的售后记录不存在')
    if case.addon_payment_id is not None and case.addon_payment_id != payment.pk:
        raise ValidationError('售后记录不属于该加购付款')
    if order.refund_records.exists() and case.addon_payment_id != payment.pk:
        from .ordinary_addon_production import eligible as ordinary_addon_eligible
        from .models import NegotiationExecution
        affected = list(NegotiationExecution.objects.filter(refund__order=order)
                        .values_list('proposal__affected_accounts', flat=True))
        if (ordinary_addon_eligible(order, allow_open=True)
                and any(isinstance(accounts, list) and payment.intent.provider_id in accounts for accounts in affected)):
            raise ValidationError('已终止主单后的加购退款须使用指定加购售后')
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    from .points_followup_handover_invalidation import may_refund_pending_target
    from .points_handover_refund_invalidation import permits_other_payment_locked
    from .points_business_access import permits as points_permits
    from .ordinary_pending_handover_refund import permits as ordinary_pending_permits
    pending = payment.handovers.filter(status='PENDING').first()
    team_pending = bool(pending and points_permits(order, phase='refund', kind='addon')
        and permits_other_payment_locked(order, payment.pk, allow_same_payment=True))
    from .points_third_handover_invalidation import permits_for_round_locked as permits_third_invalidation
    third_pending = bool(pending and points_permits(order, phase='refund', kind='transfer_addon')
                         and permits_third_invalidation(order, pending))
    if (not may_refund_pending_target(order, payment) and not ordinary_pending_permits(order, payment, pending)
            and not team_pending and not third_pending):
        assert_no_pending_handover(payment)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_third_addon_responsibilities import current as current_responsibilities
    current_responsibilities(payment, lock=True)
    return order, case, payment


def _remaining(payment):
    return sum(row.amount - row.refunded_amount for row in payment.allocations.all())


def _source_plan(payment, amount):
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    allocations = list(payment.allocations.order_by('pk'))
    try:
        planned = plan_refund([RefundablePart(row.pk, row.kind, row.amount, row.refunded_amount)
                               for row in allocations], amount)
    except FundingPlanError as exc:
        raise ValidationError(str(exc)) from exc
    return [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': planned[row.pk]}
            for row in allocations if planned[row.pk]]


def _allocate_refund_basis(amount, weights):
    """Allocate exact smallest units without ever exceeding a remaining share."""
    total = sum(weight for _, weight in weights)
    allocated = {key: amount * weight // total for key, weight in weights}
    remainder = amount - sum(allocated.values())
    ranked = sorted(weights, key=lambda row: (-(amount * row[1] % total), row[0]))
    for key, weight in ranked:
        if not remainder:
            break
        if allocated[key] < weight:
            allocated[key] += 1
            remainder -= 1
    if remainder:
        raise ValidationError('退款份额无法准确分配')
    return allocated


def _allocate_segment_burden(entries, burden, latest_first):
    """Allocate the participant's rounded excess from newest segment backwards."""
    for entry in entries:
        entry['burden'] = min(entry['refund_basis'], entry['burden_capacity'])
    extra = burden - sum(entry['burden'] for entry in entries)
    if extra < 0:
        raise ValidationError('退款承担小于原始退款份额')
    by_segment = {entry['segment_id']: entry for entry in entries}
    for segment_id in latest_first:
        entry = by_segment[segment_id]
        take = min(extra, entry['burden_capacity'] - entry['burden'])
        entry['burden'] += take
        extra -= take
    return extra


def _participants(payment, case, amount, selected):
    """Split the actual refund by agreed unpaid work; round each burden up to one coin."""
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(payment.intent.order) and payment.intent.order.refund_records.count() >= 3:
        from .points_third_addon_responsibilities import current as current_responsibilities
    else:
        from .addon_handover import current_responsibilities
    _, shares = current_responsibilities(payment, lock=True)
    people = {}
    for share in shares:
        if share['basis']:
            segments = people.setdefault(share['provider_id'], {})
            segment = segments.setdefault(share['segment_id'], {'basis': 0, 'income': 0})
            segment['basis'] += share['basis']
            segment['income'] += share['income']
    # Contractual unpaid basis and remaining displayable original value differ
    # when earlier refunds rounded a player's burden upward. Track that prior
    # difference per segment, including refunds preceding a later handover.
    historical_rounding = {}
    from .models import OrderAddonRefundParticipant
    previous = OrderAddonRefundParticipant.objects.filter(proposal__refund__payment=payment).order_by('proposal__refund__pk', 'pk')
    for person in previous:
        for entry in person.share_plan:
            key = (person.account_id, entry['segment_id'])
            historical_rounding[key] = historical_rounding.get(key, 0) + entry['burden'] - entry['refund_basis']
    if selected is None:
        if len(people) != 1:
            raise ValidationError('请选择所有承担本次退款的陪玩')
        selected = list(people)
    if (not isinstance(selected, list) or not selected or any(type(item) is not int for item in selected)
            or len(set(selected)) != len(selected) or any(item not in people for item in selected)):
        raise ValidationError('退款责任人不属于该加购的有效履约份额')
    if any(not applies_to(case, item) for item in selected):
        raise ValidationError('售后范围未覆盖全部受影响陪玩')
    selected = sorted(selected)
    total = sum(sum(row['basis'] for row in people[account].values()) for account in selected)
    if amount > total:
        raise ValidationError('退款金额超过所选陪玩的剩余加购份额')
    person_basis = {account: sum(row['basis'] for row in people[account].values()) for account in selected}
    person_refunds = _allocate_refund_basis(amount, list(person_basis.items()))
    result = []
    for account in selected:
        segments = people[account]
        basis = person_basis[account]
        refund_basis = person_refunds[account]
        burden = ((amount * basis + total * 10 - 1) // (total * 10)) * 10
        segment_refunds = _allocate_refund_basis(refund_basis,
            [(segment_id, facts['basis']) for segment_id, facts in segments.items()])
        entries = []
        for segment_id, facts in sorted(segments.items()):
            capacity = max(0, min(facts['basis'], facts['basis'] - historical_rounding.get((account, segment_id), 0)))
            entries.append({'segment_id': segment_id, 'basis': facts['basis'],
                            'income': facts['income'], 'refund_basis': segment_refunds[segment_id],
                            'burden_capacity': capacity})
        from .models import OrderParticipationSegment
        segment_rows = OrderParticipationSegment.objects.filter(pk__in=segments).order_by('-started_at', '-accepted_at', '-pk')
        latest = [row.pk for row in segment_rows]
        excess = _allocate_segment_burden(entries, burden, latest)
        result.append({'account_id': account, 'basis': basis, 'burden': burden,
                       'excess_burden': excess, 'share_plan': entries})
    return result


def _data(proposal, *, account=None, execution_income=None):
    payment = proposal.payment
    from .addon_refund_execution import enabled
    executed = hasattr(proposal, 'refund')
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    order = payment.intent.order
    from .ordinary_transfer_capability import enabled as transferred_enabled, permits as transferred_permits
    transferred = False
    if transferred_enabled():
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            transferred = transferred_permits(locked_order, allow_addon_refund=True,
                                               allow_addon_handover=True) and not locked_order.settled_at
    from .negotiation_zero import marked_execution_id
    ordinary_paid_after_main = bool(order.refund_records.count() == 1 and proposal.case.addon_payment_id == payment.pk
        and order.providers.filter(settled_at__isnull=False).exists()
        and not order.providers.filter(settled_at__isnull=False,
            provider_account_id__in=proposal.participants.values('account_id')).exists()
        and hasattr(order, 'points_settlement')
        and hasattr(order.points_settlement, 'ordinary_refund_addon_evidence'))
    production = (ordinary_addon_eligible(order, allow_open=True)
                  and (not payment.refund_records.exists() or order.refund_records.count() == 1
                       or marked_execution_id(order))
                  and not order.settled_at
                  and (ordinary_paid_after_main or not order.providers.filter(settled_at__isnull=False).exists())
                  and 0 < proposal.amount <= _remaining(payment))
    production = production or (transferred and 0 < proposal.amount <= _remaining(payment))
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_third_addon_responsibilities import current as current_responsibilities
    else:
        from .addon_handover import current_responsibilities
    shares = current_responsibilities(payment)[1] if execution_income is None else []
    people = list(proposal.participants.order_by('account_id'))
    is_provider = account is not None and account.account_type == 'PROVIDER'
    if is_provider:
        people = [row for row in people if row.account_id == account.pk]
        shares = [row for row in shares if row['provider_id'] == account.pk]
    incomes = sum(row['income'] for row in shares) if execution_income is None else execution_income
    burden = sum(row.burden for row in people)
    settled = set(payment.settlements.values_list('segment_id', flat=True)) | set(
        payment.segment_settlements.values_list('segment_id', flat=True))
    predicted_deduction = sum(min(person.burden, sum(row['income'] for row in shares
        if row['provider_id'] == person.account_id and row['segment_id'] not in settled)) for person in people)
    cross_plan = proposal.cross_pending_plan if isinstance(proposal.cross_pending_plan, dict) else None
    cross_items = cross_plan.get('items', []) if cross_plan else []
    if is_provider:
        cross_items = [item for item in cross_items if item.get('account_id') == account.pk]
    return {'id': proposal.pk, 'payment_id': payment.pk, 'case_id': proposal.case_id,
            'addon_name': payment.intent.addon_name, 'amount_raw': proposal.amount,
            'provider_burden_raw': burden,
            'rounding_surplus_raw': None if is_provider else burden - proposal.amount,
            'provider_income_raw': incomes,
            'provider_income_retained_raw': incomes if executed else incomes - predicted_deduction,
            'refund_sources': proposal.source_plan,
            'cross_pending_deductions': cross_items,
            'participants': [{'account_id': row.account_id, 'basis_raw': row.basis, 'burden_raw': row.burden,
                              'excess_burden_raw': row.excess_burden,
                              'share_plan': row.share_plan,
                              'addon_pending_deduction_raw': row.addon_pending_deduction if executed else None,
                              'addon_deductions': [{'payment_id': item.payment_id,
                                                    'addon_name': item.payment.intent.addon_name,
                                                    'segment_id': item.segment_id, 'amount_raw': item.amount}
                                                   for item in row.earning_deductions.select_related('payment__intent')
                                                   .order_by('payment_id', 'segment_id')]
                                                  if executed else None,
                              'main_pending_deduction_raw': row.main_pending_deduction if executed else None,
                              'wallet_deduction_raw': row.wallet_deduction if executed else None,
                              'deposit_deduction_raw': row.deposit_deduction if executed else None}
                             for row in people],
            'explanation': proposal.explanation, 'status': proposal.status,
            'decisions': [{'account_id': row.account_id, 'role': row.role, 'accepted': row.accepted,
                           'decided_at': row.decided_at} for row in proposal.decisions.order_by('pk')
                          if not is_provider or row.account_id == account.pk or row.role == 'BOSS'],
            'created_at': proposal.created_at, 'accepted_at': proposal.accepted_at,
            'refund_executed': executed, 'execution_available': (enabled() or production) and proposal.status == 'ACCEPTED' and not executed}


class StaffAddonRefundProposalView(APIView):
    permission_classes = [IsConsoleUser]

    @scoped_addon_refund_action
    def get(self, request, order_id, payment_id):
        if 'order:view' not in get_account_permissions(request.account) and 'order:refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权查看售后方案'}, status=403)
        rows = OrderAddonRefundProposal.objects.select_related('payment__intent').filter(
            payment_id=payment_id, payment__intent__order_id=order_id).order_by('-pk')
        return Response({'code': 0, 'data': [_data(row, account=request.account) for row in rows]})

    @transaction.atomic
    @scoped_addon_refund_action
    def post(self, request, order_id, payment_id):
        if 'order:refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权登记退款方案'}, status=403)
        body = request.data
        if not isinstance(body, Mapping):
            raise ValidationError('请填写退款方案')
        request_id, amount, explanation = body.get('request_id'), body.get('amount_raw'), body.get('explanation')
        if (not {'request_id', 'amount_raw', 'explanation'} <= set(body)
                or set(body) - {'request_id', 'amount_raw', 'explanation', 'affected_provider_account_ids', 'case_id'}
                or not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 64
                or type(amount) is not int or amount <= 0 or amount % 10
                or not isinstance(explanation, str) or not 2 <= len(explanation.strip()) <= 1000):
            raise ValidationError('请填写整数店币退款金额和协商说明')
        selected = body.get('affected_provider_account_ids')
        if selected is not None and (not isinstance(selected, list) or any(type(item) is not int for item in selected)):
            raise ValidationError('请选择有效的退款责任人')
        case_id = body.get('case_id')
        if case_id is not None and (type(case_id) is not int or case_id <= 0):
            raise ValidationError('请选择有效的售后记录')
        request_id, explanation = request_id.strip(), explanation.strip()
        order, case, payment = _locked(order_id, payment_id, case_id=case_id)
        previous = OrderAddonRefundProposal.objects.filter(payment=payment, request_id=request_id).first()
        if previous:
            if (previous.case_id != case.pk or previous.created_by_id != request.account.pk
                    or previous.amount != amount or previous.explanation != explanation
                    or (body.get('affected_provider_account_ids') is not None and
                        set(body['affected_provider_account_ids']) != set(previous.participants.values_list('account_id', flat=True)))):
                raise ValidationError('请求编号已用于其他退款方案')
            return Response({'code': 0, 'data': _data(previous, account=request.account)})
        if amount > _remaining(payment):
            raise ValidationError('退款金额超过加购未退款金额')
        people = _participants(payment, case, amount, body.get('affected_provider_account_ids'))
        from .points_service_payment import needs_integrated_processing
        from .ordinary_addon_production import eligible as ordinary_addon_eligible
        if (not needs_integrated_processing(order) and not order.refund_records.exists()
                and order.addon_intents.count() >= 1 and ordinary_addon_eligible(order, allow_open=True)):
            from .addon_handover import current_responsibilities
            from .addon_refund_execution import _main_pending_income
            from django.db.models import Sum
            from users.models import EscortProfile
            from wallet.services import get_wallet
            handover = payment.handovers.filter(status='ACCEPTED').first()
            if not handover and (len(people) != 1 or people[0]['account_id'] != payment.intent.provider_id):
                raise ValidationError('当前主单前退款责任范围待核对')
            addons = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('intent'))
            for person in people:
                account_id = person['account_id']
                own = sum(share['income'] for addon in addons for share in current_responsibilities(addon)[1]
                          if share['provider_id'] == account_id)
                used = OrderAddonRefundParticipant.objects.filter(proposal__payment__intent__order=order,
                    proposal__refund__isnull=False, account_id=account_id).aggregate(
                        total=Sum('main_pending_deduction'))['total'] or 0
                main = _main_pending_income(order, account_id) - used
                if main < 0:
                    raise ValidationError('本人主单剩余收益待核对')
                needed = max(person['burden'] - own - main, 0)
                if needed:
                    profile = EscortProfile.objects.select_for_update().filter(account_id=account_id).first()
                    if not profile:
                        raise ValidationError('陪玩资料不存在')
                    wallet = get_wallet(account_id=account_id, user_id=profile.user_id, for_update=True, create=False)
                    available = wallet.balance if wallet and wallet.is_active else 0
                    if needed > available + profile.deposit_paid:
                        raise ValidationError('陪玩待结算收益、钱包及押金不足，请先补足后退款')
                    if needed > available and not available and (not wallet or not wallet.is_active):
                        raise ValidationError('退款前须有可核验的本人有效钱包')
        if (not needs_integrated_processing(order) and order.refund_records.count() == 1
                and order.addon_intents.count() >= 1 and ordinary_addon_eligible(order, allow_open=True)):
            from .models import OrderAddonRefund, NegotiationExecution
            from .addon_refund_execution import _main_pending_income
            from .addon_handover import current_responsibilities
            from django.db.models import Sum
            from users.models import EscortProfile
            from wallet.services import get_wallet
            main_refund = order.refund_records.order_by('created_at', 'pk').first()
            if OrderAddonRefund.objects.filter(order=order, created_at=main_refund.created_at).exists():
                raise ValidationError('退款时序待核对')
            handover = payment.handovers.filter(status='ACCEPTED').first()
            if not handover and (len(people) != 1 or people[0]['account_id'] != payment.intent.provider_id):
                raise ValidationError('当前后续退款责任范围待核对')
            addons = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('intent'))
            for person in people:
                account_id = person['account_id']
                own = sum(share['income'] for addon in addons for share in current_responsibilities(addon)[1]
                          if share['provider_id'] == account_id)
                affected = NegotiationExecution.objects.filter(refund=main_refund,
                    shares__account_id=account_id).exists()
                usage = OrderAddonRefundParticipant.objects.filter(proposal__payment__intent__order=order,
                    proposal__refund__isnull=False, account_id=account_id)
                if affected:
                    usage = usage.filter(proposal__refund__created_at__gt=main_refund.created_at)
                main = _main_pending_income(order, account_id) - (usage.aggregate(
                    total=Sum('main_pending_deduction'))['total'] or 0)
                if main < 0:
                    raise ValidationError('已终止陪玩主单保留收益待核对')
                needed = max(person['burden'] - own - main, 0)
                if needed:
                    profile = EscortProfile.objects.select_for_update().filter(account_id=account_id).first()
                    if not profile:
                        raise ValidationError('陪玩资料不存在')
                    wallet = get_wallet(account_id=account_id, user_id=profile.user_id, for_update=True, create=False)
                    available = wallet.balance if wallet and wallet.is_active else 0
                    if needed > available + profile.deposit_paid:
                        raise ValidationError('陪玩待结算收益、钱包及押金不足，请先补足后退款')
                    if needed > available and not available and (not wallet or not wallet.is_active):
                        raise ValidationError('退款时零余额押金承担凭证尚未接通')
        from .points_transfer_zero_addon_settlement import guard_unpaid_participants_locked
        guard_unpaid_participants_locked(payment, people)
        current = payment.refund_proposals.filter(status__in=['PENDING', 'ACCEPTED'],
                                                   refund__isnull=True).order_by('-pk').first()
        if current and current.status == 'ACCEPTED':
            raise ValidationError('已有双方确认的加购退款方案等待处理')
        if current:
            current.status = 'SUPERSEDED'
            current.save(update_fields=['status'])
            from site_messages.addon_refund_proposal_notice import refresh_delivered
            refresh_delivered(current)
        from .addon_refund_cross_plan import capture as capture_cross_plan
        cross_plan = capture_cross_plan(order, payment, people)
        proposal = OrderAddonRefundProposal.objects.create(payment=payment, case=case,
            case_opened_at=case.created_at, case_request_id=case.client_request_id,
            created_by=request.account, request_id=request_id, amount=amount,
            source_plan=_source_plan(payment, amount), cross_pending_plan=cross_plan, explanation=explanation)
        OrderAddonRefundParticipant.objects.bulk_create([OrderAddonRefundParticipant(proposal=proposal, **person)
                                                        for person in people])
        OrderAddonRefundDecision.objects.bulk_create(
            [OrderAddonRefundDecision(proposal=proposal, account_id=payment.intent.buyer_id, role='BOSS')] +
            [OrderAddonRefundDecision(proposal=proposal, account_id=person['account_id'], role='PROVIDER') for person in people])
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='addon-refund-proposal', object_id=str(proposal.pk),
            request_body={'order_id': order.pk, 'payment_id': payment.pk, 'amount': amount,
                          'explanation': explanation}, status_code=200)
        request._request._business_audit_recorded = True
        from site_messages.addon_refund_proposal_notice import record_all as record_proposal_notices
        record_proposal_notices(proposal)
        from .notifier import notify_order_update
        transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code': 0, 'data': _data(proposal, account=request.account)})


class UserAddonRefundProposalListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @scoped_addon_refund_action
    def get(self, request, order_id, payment_id):
        rows = (OrderAddonRefundProposal.objects.select_related('payment__intent')
                .filter(payment_id=payment_id, payment__intent__order_id=order_id,
                        decisions__account=request.account).order_by('-pk'))
        return Response({'code': 0, 'data': [_data(row, account=request.account) for row in rows]})


class UserAddonRefundProposalView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def _visible(self, request, order_id, payment_id, proposal_id):
        return (OrderAddonRefundProposal.objects.select_related('payment__intent')
                .filter(pk=proposal_id, payment_id=payment_id, payment__intent__order_id=order_id,
                        decisions__account=request.account).first())

    @scoped_addon_refund_action
    def get(self, request, order_id, payment_id, proposal_id):
        proposal = self._visible(request, order_id, payment_id, proposal_id)
        if not proposal:
            return Response({'code': 404, 'msg': '方案不存在'}, status=404)
        return Response({'code': 0, 'data': _data(proposal, account=request.account)})

    @transaction.atomic
    @scoped_addon_refund_action
    def post(self, request, order_id, payment_id, proposal_id):
        if (not isinstance(request.data, Mapping) or set(request.data) != {'accepted'}
                or type(request.data['accepted']) is not bool):
            raise ValidationError('请选择同意或拒绝')
        if not self._visible(request, order_id, payment_id, proposal_id):
            return Response({'code': 404, 'msg': '方案不存在'}, status=404)
        visible = self._visible(request, order_id, payment_id, proposal_id)
        order, case, payment = _locked(order_id, payment_id, case_id=visible.case_id)
        proposal = OrderAddonRefundProposal.objects.select_for_update().get(pk=proposal_id, payment=payment, case=case)
        decision = proposal.decisions.select_for_update().get(account=request.account)
        accepted = request.data['accepted']
        if decision.accepted is not None:
            if decision.accepted != accepted:
                raise ValidationError('确认已记录，请联系客服修改方案')
            return Response({'code': 0, 'data': _data(proposal, account=request.account)})
        if proposal.status != 'PENDING' or not request.account.is_active or not request.account.can_login:
            raise ValidationError('方案已结束或账号不可用')
        if (proposal.case_opened_at != case.created_at or proposal.case_request_id != case.client_request_id
                or proposal.amount > _remaining(payment)
                or proposal.source_plan != _source_plan(payment, proposal.amount)):
            raise ValidationError('加购退款金额已变化，请联系客服')
        saved = list(proposal.participants.order_by('account_id').values('account_id', 'basis', 'burden', 'excess_burden', 'share_plan'))
        if saved != _participants(payment, case, proposal.amount, [row['account_id'] for row in saved]):
            raise ValidationError('加购履约份额已变化，请联系客服重新确认')
        from .addon_refund_cross_plan import require_current
        require_current(proposal, saved)
        from .points_transfer_zero_addon_settlement import guard_unpaid_participants_locked
        guard_unpaid_participants_locked(payment, saved)
        decision.accepted, decision.decided_at = accepted, timezone.now()
        decision.save(update_fields=['accepted', 'decided_at'])
        if not accepted:
            proposal.status = 'REJECTED'
        elif not proposal.decisions.filter(accepted__isnull=True).exists():
            proposal.status, proposal.accepted_at = 'ACCEPTED', decision.decided_at
        proposal.save(update_fields=['status', 'accepted_at'])
        from site_messages.addon_refund_proposal_notice import refresh_delivered
        refresh_delivered(proposal)
        from .notifier import notify_order_update
        transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code': 0, 'data': _data(proposal, account=request.account), 'msg': '确认已记录，退款尚未执行'})


class StaffAddonRefundExecuteView(APIView):
    permission_classes = [IsConsoleUser]

    @protect_addon_refund
    @transaction.atomic
    def post(self, request, order_id, payment_id, proposal_id):
        if 'order:refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权执行退款'}, status=403)
        from .addon_refund_execution import execute
        refund, execution_income = execute(order_id, payment_id, proposal_id, operator=request.account,
                                           legacy_operator=request.legacy_user)
        from console.models import AdminAuditLog
        if not AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(refund.pk)).exists():
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
                path=request.path[:255], resource='addon-refund', object_id=str(refund.pk),
                request_body={'order_id': order_id, 'payment_id': payment_id, 'proposal_id': proposal_id,
                              'amount': refund.amount}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _data(refund.proposal, account=request.account,
                                                  execution_income=execution_income)})
