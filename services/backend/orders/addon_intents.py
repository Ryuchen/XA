"""Persist reviewed add-on intentions without posting a payment."""

import hashlib
import json

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from stores.db import transaction
from .addon_offers import AddonSelectionError, _owner_order, validate_selection
from .models import (Order, OrderAddonIntent, OrderSegmentFinancialCoverage,
                     OrderSegmentFinancialSnapshot, ServiceAddonConfiguration)
from .settlement import compute_provider_income


SELECTION_FIELDS = {'addon_id', 'unit', 'quantity', 'configuration_version', 'provider_account_id'}


def _clean(data, *, staff):
    extra = {'request_id', 'expected_amount', 'gift_balance_amount', 'compensation_balance_amount'}
    if staff:
        extra |= {'confirmation_method', 'confirmation_content', 'review_note'}
    if not isinstance(data, dict) or set(data) != SELECTION_FIELDS | extra:
        raise AddonSelectionError('请完整填写加购项目、金额和确认信息', 400)
    request_id = data['request_id']
    if not isinstance(request_id, str) or not request_id.strip() or request_id != request_id.strip() or len(request_id) > 80:
        raise AddonSelectionError('请求编号无效', 400)
    for field in ('expected_amount', 'gift_balance_amount', 'compensation_balance_amount'):
        value = data[field]
        if type(value) is not int or not 0 <= value <= 2147483647 or value % 10:
            raise AddonSelectionError('金额须为非负整数店币', 400)
    if data['gift_balance_amount'] + data['compensation_balance_amount'] > data['expected_amount']:
        raise AddonSelectionError('赠送和赔付不能超过应付金额', 400)
    if staff:
        for field, limit in (('confirmation_method', 50), ('confirmation_content', 500), ('review_note', 500)):
            value = data[field]
            if not isinstance(value, str) or value != value.strip() or not 2 <= len(value) <= limit:
                raise AddonSelectionError('请记录老板确认方式、内容和客服核验依据', 400)
    return {key: data[key] for key in SELECTION_FIELDS}


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _commission(segment, *, allow_refund_review=False, lock=True):
    from .segment_financial_capture import TRANSFER_RATE_REVIEW
    snapshots = OrderSegmentFinancialSnapshot.objects
    coverages = OrderSegmentFinancialCoverage.objects
    if lock:
        snapshots, coverages = snapshots.select_for_update(), coverages.select_for_update()
    snap = snapshots.filter(segment=segment).first()
    coverage = coverages.filter(attribution=segment.attribution).first()
    from .delta_personal_commission import verify_segment_evidence
    if (not snap or not coverage or coverage.deal_amount <= 0
            or not (coverage.status == 'ACTIVE' or
                    (coverage.status == 'REVIEW' and (coverage.review_reason == TRANSFER_RATE_REVIEW or
                     (allow_refund_review and coverage.review_reason == '协商退款后原价份额待核对'))))
            or snap.commission_type not in ('PERCENT', 'FIXED')
            or not verify_segment_evidence(segment, snap)
            or snap.income_at_entry != compute_provider_income(snap.incoming_basis, snap.commission_type,
                                                               snap.commission_rate, snap.commission_fixed)
            or (snap.commission_type == 'PERCENT' and (snap.commission_fixed or snap.fixed_rule_total is not None))
            or (snap.commission_type == 'FIXED' and (snap.commission_rate or snap.fixed_rule_total is None))):
        raise AddonSelectionError('主单陪玩抽成快照待核对')
    return {'segment_snapshot_id': snap.pk, 'type': snap.commission_type, 'rate': snap.commission_rate,
            'fixed_rule_total': snap.fixed_rule_total, 'order_deal_amount': coverage.deal_amount,
            'incoming_basis': snap.incoming_basis, 'commission_fixed': snap.commission_fixed,
            'source': snap.commission_source, 'game_rules': snap.game_commission_snapshot}


def _data(intent, *, actor, payment_table_known=None):
    payment = None
    from .addon_payment import payment_table_exists, verify_receipt
    from .models import OrderAddonPayment
    available = payment_table_exists() if payment_table_known is None else payment_table_known
    if available:
        payment = OrderAddonPayment.objects.select_related('intent', 'buyer_transaction__wallet').filter(intent=intent).first()
        if payment:
            verify_receipt(payment)
    from .addon_payment import payment_available
    can_pay = bool(available and not payment and payment_available(intent, actor=actor))
    return {'id': intent.pk, 'order_id': intent.order_id, 'addon_id': intent.addon_id,
            'addon_name': intent.addon_name, 'provider_account_id': intent.provider_id,
            'segment_id': intent.segment_id, 'unit': intent.unit, 'quantity': intent.quantity,
            'unit_price_raw': intent.unit_price, 'amount_raw': intent.amount,
            'source_raw': {'principal': intent.principal, 'gift': intent.gift, 'compensation': intent.compensation},
            'configuration_version': intent.config_version, 'condition_mode': intent.condition_mode,
            'conditions': intent.condition_snapshot, 'confirmation': intent.confirmation,
            'request_id': intent.request_id, 'created_at': intent.created_at,
            'payment_available': can_pay, 'paid': bool(payment), 'payment_id': payment.pk if payment else None}


def _list(request, order):
    before = request.query_params.get('before_id')
    if before is not None and (not before.isascii() or not before.isdigit() or len(before) > 19
                               or not 0 < int(before) <= 9223372036854775807):
        return Response({'code': 400, 'msg': '分页参数无效'}, status=400)
    query = order.addon_intents.select_related('segment').order_by('-pk')
    if before:
        query = query.filter(pk__lt=int(before))
    rows = list(query[:51])
    from .addon_payment import payment_table_exists, verify_order_debit_links
    available = payment_table_exists()
    verify_order_debit_links(order, table_exists=available)
    return Response({'code': 0, 'data': {'items': [_data(row, actor=request.account, payment_table_known=available) for row in rows[:50]],
        'next_before_id': rows[49].pk if len(rows) > 50 else None}})


def _create(request, order, *, staff):
    from django.conf import settings
    if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        return _create_scoped(request, order, staff=staff)
    from club_accounts.models import ClubAccount
    from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                   lock_provider_operation)
    provider_id = request.data.get('provider_account_id')
    if type(provider_id) is not int or provider_id <= 0:
        return Response({'code': 400, 'msg': '请选择有效陪玩'}, status=400)
    account = ClubAccount.objects.filter(pk=provider_id, account_type=ClubAccount.AccountType.PROVIDER).first()
    if not account:
        return Response({'code': 400, 'msg': '陪玩账号待核对'}, status=400)
    try:
        ensure_provider_lifecycle(account)
    except ProviderLifecycleError:
        return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验'}, status=503)
    with transaction.atomic():
        try:
            lock_provider_operation(account)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验'}, status=503)
        return _create_scoped(request, order, staff=staff, lifecycle=account)


def _create_scoped(request, order, *, staff, lifecycle=None):
    from .points_service_payment import needs_integrated_processing
    if not needs_integrated_processing(order) or order.refund_records.count() < 4:
        return _create_locked(request, order, staff=staff, lifecycle=lifecycle)
    from .points_transfer_refund_access import after_transfer
    from rest_framework.exceptions import ValidationError
    try:
        with transaction.atomic():
            with after_transfer(order.pk, phase='refund', allow_addon_completion=True):
                return _create_locked(request, order, staff=staff, lifecycle=lifecycle)
    except ValidationError:
        return Response({'code': 409, 'msg': '当前订单资金或履约状态待核对'}, status=409)


def _create_locked(request, order, *, staff, lifecycle=None):
    try:
        selection = _clean(request.data, staff=staff)
        fingerprint = _hash({'actor_id': request.account.pk, 'staff': staff, 'body': request.data})
        with transaction.atomic():
            order = Order.objects.select_for_update().select_related('service', 'customer_account').get(pk=order.pk)
            from .addon_payment import payment_table_exists, verify_order_debit_links
            available = payment_table_exists()
            verify_order_debit_links(order, table_exists=available, lock=True)
            previous = OrderAddonIntent.objects.select_related('segment').filter(
                order=order, request_id=request.data['request_id']).first()
            if previous:
                if previous.operator_id != request.account.pk or previous.request_hash != fingerprint:
                    raise AddonSelectionError('请求编号已用于不同的加购内容')
                return Response({'code': 0, 'data': _data(previous, actor=request.account, payment_table_known=available)})
            if not staff and (order.customer_account_id != request.account.pk
                              or order.business_cycle != request.account.business_cycle):
                raise AddonSelectionError('订单不存在', 404)
            if not order.customer_account_id or order.business_cycle != order.customer_account.business_cycle:
                raise AddonSelectionError('订单不存在', 404)
            config, option, segment, amount = validate_selection(order, selection, lock=True)
            if lifecycle:
                from club_accounts.provider_lifecycle import provider_membership_current
                if (segment.provider_account_id != lifecycle.pk
                        or segment.provider_business_cycle != lifecycle.business_cycle
                        or not provider_membership_current(lifecycle)):
                    raise AddonSelectionError('加购陪玩履约来源周期已变化，请重新核对')
            if amount != request.data['expected_amount']:
                raise AddonSelectionError('加购价格已变化，请重新核对')
            if not staff and config.condition_mode != ServiceAddonConfiguration.ConditionMode.NONE:
                raise AddonSelectionError('该加购需要联系客服核实')
            if config.condition_mode == ServiceAddonConfiguration.ConditionMode.NONE and config.conditions:
                raise AddonSelectionError('加购条件配置待核对')
            from .points_service_payment import needs_integrated_processing
            round_review = needs_integrated_processing(order) and order.refund_records.count() >= 4
            if round_review:
                from .points_round_funding import verify_active_segment_locked
                verify_active_segment_locked(order, segment)
            commission = _commission(segment, allow_refund_review=round_review)
            confirmation = ({'method': request.data['confirmation_method'],
                             'content': request.data['confirmation_content'],
                             'review_note': request.data['review_note'],
                             'recorded_at': timezone.now().isoformat()} if staff else {})
            gift, compensation = request.data['gift_balance_amount'], request.data['compensation_balance_amount']
            row = OrderAddonIntent.objects.create(order=order, segment=segment, provider_id=segment.provider_account_id, addon=config.addon,
                buyer=order.customer_account, operator=request.account, request_id=request.data['request_id'],
                request_hash=fingerprint, unit=option.unit, quantity=selection['quantity'], unit_price=option.price,
                amount=amount, principal=amount-gift-compensation, gift=gift, compensation=compensation,
                config_version=config.version, condition_mode=config.condition_mode,
                condition_snapshot=config.conditions, addon_name=config.addon.name,
                commission_snapshot=commission, confirmation=confirmation)
            if staff:
                from console.models import AdminAuditLog
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
                    path=request.path[:255], resource='order-addon-intent', object_id=str(row.pk),
                    request_body={'order_id': order.pk, 'request_id': row.request_id, 'selection': selection,
                                  'amount': amount, 'gift': gift, 'compensation': compensation,
                                  'confirmation': confirmation}, status_code=200)
                request._request._business_audit_recorded = True
            return Response({'code': 0, 'data': _data(row, actor=request.account, payment_table_known=available)})
    except Order.DoesNotExist:
        return Response({'code': 404, 'msg': '订单不存在'}, status=404)
    except AddonSelectionError as exc:
        return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)


class OrderAddonIntentView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        order = _owner_order(request.account, order_id)
        if not order:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        return _create(request, order, staff=False)

    def get(self, request, order_id):
        order = _owner_order(request.account, order_id)
        if not order:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        return _list(request, order)


class StaffOrderAddonIntentView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, order_id):
        if not {'order:view', 'order:dispatch'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看加购申请'}, status=403)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        order = Order.objects.filter(pk=order_id).first()
        if not order:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        return _list(request, order)

    def post(self, request, order_id):
        if 'order:dispatch' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权代老板发起加购'}, status=403)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        order = Order.objects.select_related('customer_account').filter(pk=order_id).first()
        if not order or not order.customer_account_id or order.business_cycle != order.customer_account.business_cycle:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        return _create(request, order, staff=True)


class OrderAddonPaymentView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id, intent_id):
        order = _owner_order(request.account, order_id)
        if not order or not OrderAddonIntent.objects.filter(pk=intent_id, order=order).exists():
            return Response({'code': 404, 'msg': '加购申请不存在'}, status=404)
        from .addon_payment import pay
        payment = pay(intent_id, actor=request.account)
        return Response({'code': 0, 'data': _data(payment.intent, actor=request.account)})


class StaffOrderAddonPaymentView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request, order_id, intent_id):
        if 'order:dispatch' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权代老板支付加购'}, status=403)
        if not OrderAddonIntent.objects.filter(pk=intent_id, order_id=order_id).exists():
            return Response({'code': 404, 'msg': '加购申请不存在'}, status=404)
        from .addon_payment import pay
        payment = pay(intent_id, actor=request.account)
        return Response({'code': 0, 'data': _data(payment.intent, actor=request.account)})
