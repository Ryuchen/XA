"""Preview and atomically snapshot clocks for eligible unsettled legacy orders."""
import hashlib
import json

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.context import current_store
from stores.db import transaction
from users.models import EscortProfile
from wallet.club_rules import DEPOSIT_PERCENT_KEY
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Transaction, Wallet
from wallet.settlement_clocks import CONFIG_KEY, parse_rules
from .financial_lock import lock_order_money
from .models import LegacyClockCutover, Order, OrderRoom
from .points_service_payment import needs_integrated_processing
from .reservation_permissions import staff_can


def _time(value):
    return value.isoformat() if value else None


def _preview_locked(order):
    """Read facts under the order lock; no accounting or configuration writes."""
    configs = {row.key: row for row in SystemConfig.objects.select_for_update().filter(
        key__in=[CONFIG_KEY, DEPOSIT_PERCENT_KEY]).order_by('key')}
    clock_row, deposit_row = configs.get(CONFIG_KEY), configs.get(DEPOSIT_PERCENT_KEY)
    problems, rules = [], None
    try:
        if not clock_row:
            raise ValueError()
        rules = parse_rules(clock_row.value)
    except ValueError:
        problems.append('门店售后与收益冻结时限未配置或无效')
    raw = deposit_row.value if deposit_row else None
    valid_deposit_config = (isinstance(raw, str) and raw.isascii() and raw.isdigit()
                            and len(raw) <= 10 and int(raw) <= 100)
    if order.service_deposit_percent is not None:
        percent, deposit_source = order.service_deposit_percent, 'PAYMENT_SNAPSHOT'
        if not 0 <= percent <= 100:
            problems.append('原订单单抵押金比例无效，须人工核对')
    elif valid_deposit_config:
        percent, deposit_source = int(raw), 'STORE_AT_CUTOVER'
    else:
        problems.append('门店单抵押金比例未配置或无效')
        percent, deposit_source = None, 'UNAVAILABLE'
    refund_count = order.refund_records.count()
    completed_legacy = order.status == Order.Status.COMPLETED
    if completed_legacy:
        # The existing historical deadline is completion + 72 hours. It has
        # already started and must not adopt the store's current duration.
        aftersale_hours, aftersale_source = 72, 'LEGACY_DEADLINE'
    else:
        aftersale_hours = rules['aftersale_hours'] if rules else None
        aftersale_source = 'STORE_AT_CUTOVER' if rules else 'UNAVAILABLE'
    completion_invalid = completed_legacy and (
        not order.completed_at or not order.in_service_at
        or order.completed_at < order.created_at or order.completed_at < order.in_service_at
        or order.completed_at > timezone.now())
    if (order.room_required_snapshot is not None or order.is_gift_snapshot or order.source_order_id
            or OrderRoom.objects.filter(order=order).exists()
            or order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or (order.status == Order.Status.GRABBED and order.in_service_at)
            or (order.status == Order.Status.IN_SERVICE and not order.in_service_at)
            or (not completed_legacy and order.completed_at) or completion_invalid
            or order.payment_status != Order.PaymentStatus.PAID or order.settled_at
            or refund_count or needs_integrated_processing(order)):
        problems.append('订单不是可直接接管的未结历史普通服务单')
    if (order.service_clock_version is not None or order.service_aftersale_hours is not None
            or order.service_freeze_hours is not None):
        problems.append('订单已有或部分已有时钟快照，不能覆盖')
    if order.settlement_mode not in (Order.SettlementMode.COMPLETION, Order.SettlementMode.REPORT_APPROVAL):
        problems.append('原结算模式待核对')
    customer_wallet = Wallet.objects.filter(user_id=order.customer_id, account_id=order.customer_account_id).first()
    if (not order.customer_id or not order.customer_account_id
            or not LegacyAccountMap.objects.filter(account_id=order.customer_account_id,
                                                    legacy_user_id=order.customer_id).exists()
            or not customer_wallet):
        problems.append('老板账号或钱包归属待核对')
    payments = list(order.transactions.filter(tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS,
        reversal_of__isnull=True).order_by('pk'))
    if (len(payments) != 1 or not customer_wallet or payments[0].wallet_id != customer_wallet.pk
            or payments[0].amount != -order.amount
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        problems.append('历史付款流水不完整或存在冲正，须人工核对')
    assignments = list(order.providers.select_for_update().order_by('pk'))
    active = [row for row in assignments if row.released_at is None]
    required = 2 if order.escort_mode == Order.EscortMode.DOUBLE else 1
    pairs = {(row.provider_id, row.provider_account_id) for row in active}
    account_ids = [row.provider_account_id for row in active if row.provider_account_id]
    profiles = set(EscortProfile.objects.filter(account_id__in=account_ids).values_list('user_id', 'account_id'))
    mappings = set(LegacyAccountMap.objects.filter(account_id__in=account_ids).values_list('legacy_user_id', 'account_id'))
    if (len(active) != required or len(pairs) != required or len(account_ids) != required
            or order.provider_account_id not in account_ids or profiles != pairs or not pairs.issubset(mappings)
            or any(row.settled_at for row in assignments)):
        problems.append('陪玩身份、参与份额或已结算记录待核对')
    relevant = [row for row in assignments if row.settlement_base > 0 or not row.released_at]
    referral = order.inviter_commission if order.inviter_id or order.inviter_account_id else 0
    if (sum(row.settlement_base for row in relevant) != order.amount
            or sum(row.provider_income for row in relevant) != order.provider_income
            or (order.inviter_commission and not (order.inviter_id or order.inviter_account_id))
            or order.provider_income + referral + order.shop_income != order.amount):
        problems.append('原订单参与份额或分账快照不一致，须人工核对')
    reports = list(ProviderReport.objects.select_for_update().filter(order=order).order_by('pk'))
    known_pairs = {(row.provider_id, row.provider_account_id) for row in assignments}
    shares = {(row.provider_id, row.provider_account_id): row for row in assignments}
    if any((row.provider_id, row.provider_account_id) not in known_pairs or row.transaction_id
            or (row.status in ('PENDING', 'APPROVED') and not row.submitted_at)
            or (row.status == 'APPROVED' and (not row.audited_at or not (row.auditor_id or row.auditor_account_id)
                or (row.submitted_at and row.audited_at < row.submitted_at) or row.audited_at > timezone.now()
                or ((row.provider_id, row.provider_account_id) in shares and
                    (row.amount != shares[(row.provider_id, row.provider_account_id)].settlement_base
                     or row.payout_amount != shares[(row.provider_id, row.provider_account_id)].provider_income
                     or row.commission_rate != shares[(row.provider_id, row.provider_account_id)].commission_rate))))
            or (row.submitted_at and (row.submitted_at < order.created_at or row.submitted_at < row.created_at
                                      or row.submitted_at > timezone.now()))
            for row in reports):
        problems.append('历史报单提交时间或收益记录待核对')
    holds = list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk'))
    if (holds
            or order.transactions.filter(status=Transaction.Status.SUCCESS,
                tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME]).exists()):
        problems.append('已有历史收益或冻结记录，须保留原截止并人工核对')
    unlinked_count = IncomeHold.objects.filter(Q(wallet__account_id__in=account_ids) |
            Q(wallet__user_id__in=[row.provider_id for row in active if row.provider_id]),
            income_transaction__order__isnull=True, released_at__isnull=True, cancelled_at__isnull=True).count()
    if unlinked_count:
        problems.append('陪玩存在无法归属订单的历史冻结，须人工核对')
    cases = list(order.aftersales.select_for_update().order_by('pk'))
    facts = {'store': getattr(current_store(), 'slug', None), 'order_id': order.pk, 'status': order.status,
        'updated_at': _time(order.updated_at), 'payment_status': order.payment_status,
        'settlement_mode': order.settlement_mode, 'customer_account_id': order.customer_account_id,
        'refund_count': refund_count, 'in_service_at': _time(order.in_service_at),
        'completed_at': _time(order.completed_at), 'aftersale_hours': aftersale_hours,
        'aftersale_source': aftersale_source,
        'payment_facts': [{'id': row.pk, 'wallet_id': row.wallet_id, 'amount': row.amount,
                           'balance_before': row.balance_before, 'balance_after': row.balance_after,
                           'reversed': row.reversals.exists()} for row in payments],
        'order_amount': order.amount, 'provider_income': order.provider_income,
        'inviter_commission': referral, 'shop_income': order.shop_income,
        'previous_report_ready_at': _time(order.report_ready_at),
        'previous_consumption_ready_at': _time(order.consumption_ready_at),
        'aftersales': [{'id': row.pk, 'status': row.status, 'created_at': _time(row.created_at),
                        'affected_provider_account_ids': row.affected_provider_account_ids} for row in cases],
        'income_holds': [{'id': row.pk, 'amount': row.amount, 'release_at': _time(row.release_at),
                          'released_at': _time(row.released_at), 'cancelled_at': _time(row.cancelled_at)} for row in holds],
        'unlinked_open_holds': unlinked_count,
        'assignments': [{'id': row.pk, 'account_id': row.provider_account_id, 'user_id': row.provider_id,
            'settlement_base': row.settlement_base, 'provider_income': row.provider_income,
            'commission_type': row.commission_type, 'commission_rate': row.commission_rate,
            'commission_fixed': row.commission_fixed, 'released_at': _time(row.released_at),
            'settled_at': _time(row.settled_at)} for row in assignments],
        'reports': [{'id': row.pk, 'account_id': row.provider_account_id, 'user_id': row.provider_id,
                     'status': row.status, 'created_at': _time(row.created_at), 'updated_at': _time(row.updated_at),
                     'amount': row.amount, 'commission_rate': row.commission_rate, 'payout_amount': row.payout_amount,
                     'submitted_at': _time(row.submitted_at), 'audited_at': _time(row.audited_at),
                     'auditor_id': row.auditor_id, 'auditor_account_id': row.auditor_account_id,
                     'transaction_id': row.transaction_id} for row in reports],
        'clock_config': rules, 'deposit_percent': percent, 'deposit_source': deposit_source,
        'clock_config_updated_at': _time(clock_row.updated_at) if clock_row else None,
        'deposit_config_updated_at': _time(deposit_row.updated_at) if deposit_row else None, 'problems': problems}
    fingerprint = hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':')).encode('utf-8')).hexdigest()
    return facts, fingerprint


def _record_data(row):
    return {'order_id': row.order_id, 'operator_id': row.operator_id, 'client_request_id': row.request_id,
            'plan_fingerprint': row.plan_fingerprint, 'previous': row.previous, 'rules': row.rules,
            'recorded_at': row.recorded_at}


def _record_matches(order, row):
    rules = row.rules
    return (isinstance(rules, dict) and order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL
            and order.service_aftersale_hours == rules.get('aftersale_hours')
            and order.service_freeze_hours == rules.get('income_freeze_hours')
            and order.service_clock_version == rules.get('version')
            and order.service_deposit_percent == rules.get('deposit_percent'))


class LegacyClockCutoverView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def _allowed(self, account):
        return staff_can(account, 'order:view') and staff_can(account, 'deposit_rule:edit')

    @transaction.atomic
    def get(self, request, order_id):
        if not self._allowed(request.account):
            return Response({'code': 403, 'msg': '无权核对历史订单切换'}, status=403)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        record = LegacyClockCutover.objects.filter(order=order).first()
        if record:
            return Response({'code': 0, 'data': {'state': 'COMPLETED' if _record_matches(order, record) else 'REVIEW',
                                                  'record': _record_data(record)}})
        facts, fingerprint = _preview_locked(order)
        return Response({'code': 0, 'data': {'state': 'REVIEW' if facts['problems'] else 'READY',
            'facts': facts, 'plan_fingerprint': fingerprint}})

    @transaction.atomic
    def post(self, request, order_id):
        if not self._allowed(request.account):
            return Response({'code': 403, 'msg': '无权切换历史订单'}, status=403)
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) != {'plan_fingerprint', 'client_request_id'}
                or not isinstance(request.data['plan_fingerprint'], str) or len(request.data['plan_fingerprint']) != 64
                or not isinstance(request.data['client_request_id'], str)
                or not 1 <= len(request.data['client_request_id']) <= 64
                or request.data['client_request_id'] != request.data['client_request_id'].strip()):
            return Response({'code': 400, 'msg': '请先核对预览并填写请求编号'}, status=400)
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        existing = LegacyClockCutover.objects.select_for_update().filter(order=order).first()
        if existing:
            if (existing.operator_id != request.account.pk or existing.request_id != request.data['client_request_id']
                    or existing.plan_fingerprint != request.data['plan_fingerprint']):
                return Response({'code': 409, 'msg': '订单已有切换记录，请查看原结果'}, status=409)
            if not _record_matches(order, existing):
                return Response({'code': 409, 'msg': '订单时钟与原切换记录不一致，请人工核对'}, status=409)
            return Response({'code': 0, 'data': _record_data(existing), 'msg': '历史订单已切换'})
        lock_order_money()
        facts, fingerprint = _preview_locked(order)
        if facts['problems'] or fingerprint != request.data['plan_fingerprint']:
            return Response({'code': 409, 'msg': '订单或门店配置已变化，请重新核对',
                             'data': {'problems': facts['problems']}}, status=409)
        clock = facts['clock_config']
        order.settlement_mode = Order.SettlementMode.REPORT_APPROVAL
        order.service_aftersale_hours, order.service_freeze_hours = facts['aftersale_hours'], clock['income_freeze_hours']
        order.service_clock_version, order.service_deposit_percent = clock['version'], facts['deposit_percent']
        order.save(update_fields=['settlement_mode', 'service_aftersale_hours', 'service_freeze_hours',
                                  'service_clock_version', 'service_deposit_percent', 'updated_at'])
        from engagement.confirmation import refresh_report_clock
        refresh_report_clock(order)
        rules = {**clock, 'aftersale_hours': facts['aftersale_hours'], 'aftersale_source': facts['aftersale_source'],
                 'deposit_percent': facts['deposit_percent'], 'deposit_source': facts['deposit_source']}
        record = LegacyClockCutover.objects.create(order=order, operator=request.account,
            request_id=request.data['client_request_id'], plan_fingerprint=fingerprint, previous=facts, rules=rules)
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='legacy-clock-cutover', object_id=str(order.pk),
            request_body={'client_request_id': record.request_id, 'plan_fingerprint': fingerprint,
                          'previous': facts, 'rules': rules}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _record_data(record), 'msg': '历史订单时钟已切换'})
