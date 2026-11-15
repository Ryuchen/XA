"""Store and verify the itemized sales basis captured at an UNKNOWN decision."""
import hashlib
import json

from django.db.models import Q
from rest_framework.exceptions import ValidationError


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def capture(account, *, rules, source_order=None):
    from orders.models import Order, OrderAddonConsumption
    orders = Order.objects.filter(customer_account_id=account.pk, business_cycle=account.business_cycle,
                                  is_gift_snapshot=False)
    mains = orders.filter(Q(consumption_confirmed_at__isnull=False) | Q(funding_benefits_resolved_at__isnull=False),
                          settled_at__isnull=False, payment_status__in=['PAID', 'PARTIALLY_REFUNDED']).order_by('pk')
    main_items = []
    for order in mains:
        refunds = list(order.refund_records.order_by('pk').values_list('pk', 'amount'))
        main_items.append({'order_id': order.pk, 'paid': order.amount,
                           'refunds': [[pk, amount] for pk, amount in refunds]})
    addons = OrderAddonConsumption.objects.filter(account_id=account.pk, business_cycle=account.business_cycle,
        payment__intent__order__customer_account_id=account.pk,
        payment__intent__order__business_cycle=account.business_cycle,
        payment__intent__order__is_gift_snapshot=False).select_related('payment__intent').order_by('pk')
    addon_items = []
    for addon in addons:
        refunds = list(addon.payment.refund_records.order_by('pk').values_list('pk', 'amount'))
        # Confirmation itself requires the paid segments to match the real
        # responsibilities. Preserve those actual settlement rows, including
        # zero-income rows, rather than reconstructing today's ownership.
        ordinary = list(addon.payment.settlements.filter(settled_at__lte=addon.confirmed_at)
                        .order_by('pk').values_list('pk', flat=True))
        segmented = list(addon.payment.segment_settlements.filter(settled_at__lte=addon.confirmed_at)
                         .order_by('pk').values_list('pk', flat=True))
        addon_items.append({'consumption_id': addon.pk, 'payment_id': addon.payment_id,
                            'order_id': addon.payment.intent.order_id, 'paid': addon.paid_units,
                            'refunds': [[pk, amount] for pk, amount in refunds],
                            'settlements': ordinary, 'segment_settlements': segmented})
    excluded = None
    if source_order is not None and source_order.pk not in {item['order_id'] for item in main_items}:
        source = orders.filter(pk=source_order.pk, payment_status='REFUNDED', settled_at__isnull=False).first()
        if source and source.amount > 0:
            refunds = list(source.refund_records.order_by('pk').values_list('pk', 'amount'))
            if refunds and sum(amount for _, amount in refunds) == source.amount:
                excluded = {'order_id': source.pk, 'paid': source.amount,
                            'refunds': [[pk, amount] for pk, amount in refunds]}
    return {'version': 1, 'account_id': str(account.pk), 'business_cycle': account.business_cycle,
            'mains': main_items, 'addons': addon_items, 'rules': rules, 'excluded_source': excluded}


def metrics(snapshot):
    mains, addons = snapshot['mains'], snapshot['addons']
    total = sum(x['paid'] - sum(r[1] for r in x['refunds']) for x in mains + addons)
    orders = {x['order_id'] for x in mains}
    orders.update(x['order_id'] for x in addons if x['paid'] > sum(r[1] for r in x['refunds']))
    return {'amount': total, 'count': len(orders)}


def verify_locked(event):
    from orders.models import Order, OrderAddonConsumption
    from wallet.models import Transaction
    from .license_points_review import _verify_award_sources
    from orders.addon_payment import verify_receipt
    data = event.sales_snapshot
    if (not isinstance(data, dict) or data.get('version') != 1 or digest(data) != event.sales_snapshot_hash
            or data.get('account_id') != str(event.account_id) or data.get('business_cycle') != event.business_cycle
            or not all(isinstance(data.get(k), list) for k in ('mains', 'addons', 'rules'))):
        raise ValidationError('成就原实销清单缺失或摘要不一致')
    try:
        excluded = data.get('excluded_source')
        if excluded is not None and (not isinstance(excluded, dict)
                or any(item['order_id'] == excluded.get('order_id') for item in data['mains'])
                or type(excluded.get('paid')) is not int or excluded['paid'] <= 0
                or not isinstance(excluded.get('refunds'), list)
                or not excluded['refunds']
                or sum(row[1] for row in excluded['refunds']) != excluded['paid']):
            raise ValidationError('成就零净额事件来源异常')
        excluded_items = [excluded] if excluded is not None else []
        for item in data['mains'] + data['addons'] + excluded_items:
            if (type(item['paid']) is not int or item['paid'] < 0 or not isinstance(item['refunds'], list)
                    or any(not isinstance(row, list) or len(row) != 2 or type(row[0]) is not int
                           or type(row[1]) is not int or row[1] < 0 for row in item['refunds'])
                    or len({row[0] for row in item['refunds']}) != len(item['refunds'])
                    or sum(row[1] for row in item['refunds']) > item['paid']):
                raise ValidationError('成就原付款或退款金额异常')
        if metrics(data) != event.metrics:
            raise ValidationError('成就原实销汇总不一致')
        if (len({x['order_id'] for x in data['mains']}) != len(data['mains'])
                or len({x['consumption_id'] for x in data['addons']}) != len(data['addons'])):
            raise ValidationError('成就原实销清单重复')
        for item in data['mains'] + excluded_items:
            order = Order.objects.filter(pk=item['order_id'], customer_account_id=event.account_id,
                business_cycle=event.business_cycle, is_gift_snapshot=False).first()
            if (not order or order.amount != item['paid'] or not order.settled_at
                    or order.settled_at > event.decided_at
                    or not order.funding_benefits_resolved_at
                    or order.funding_benefits_resolved_at > event.decided_at):
                raise ValidationError('成就主单原实销时点待核对')
            refunds = list(order.refund_records.filter(created_at__lte=event.decided_at)
                           .select_related('customer_transaction__wallet').order_by('pk'))
            if [[row.pk, row.amount] for row in refunds] != item['refunds']:
                raise ValidationError('成就主单原退款记录待核对')
            from orders.models import OrderPointsRefund
            for refund in refunds:
                points = OrderPointsRefund.objects.select_related('coin_transaction__wallet', 'redemption').filter(
                    refund=refund).first()
                credit = points.coin_transaction if points else refund.customer_transaction
                if points and (points.redemption.order_id != order.pk or points.coin_units != refund.amount):
                    raise ValidationError('成就主单积分退款来源待核对')
                if not points:
                    from orders.models import NegotiationExecution
                    executions = list(NegotiationExecution.objects.select_related(
                        'customer_transaction__wallet', 'proposal__case__order', 'refund').filter(
                            refund=refund).order_by('pk')[:2])
                    if executions:
                        if len(executions) != 1:
                            raise ValidationError('成就协商退款执行凭证重复')
                        from orders.negotiation_execution import verify_execution
                        execution = executions[0]
                        verify_execution(execution)
                        if credit and credit.pk != execution.customer_transaction_id:
                            raise ValidationError('成就协商退款流水关联不一致')
                        credit = execution.customer_transaction
                if refund.amount and (not credit or credit.order_id != order.pk
                        or credit.wallet.account_id != event.account_id or credit.tx_type != Transaction.TxType.REFUND
                        or credit.status != Transaction.Status.SUCCESS or credit.amount != refund.amount
                        or credit.balance_after != credit.balance_before + credit.amount
                        or credit.created_at > event.decided_at):
                    raise ValidationError('成就主单逐笔退款流水待核对')
            _verify_award_sources(order)
            # The caller holds the store's order-money mutex. Reuse the
            # source-composition replay directly: unlike the endpoint wrapper
            # it does not acquire another order lock after that mutex.
            if OrderPointsRefund.objects.filter(redemption__order=order).exists():
                from orders.points_service_refund import _plan as verify_points_refund_history
                receipt, history, _ = verify_points_refund_history(order, None, None, history_only=True)
                if (receipt.order_id != order.pk
                        or {row.refund_id for row in history if row.refund.created_at <= event.decided_at}
                           != {row.pk for row in refunds}):
                    raise ValidationError('成就积分退款原时点凭证待核对')
            if not Transaction.objects.filter(order=order, wallet__account_id=event.account_id,
                    tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS,
                    amount=-item['paid'], created_at__lte=event.decided_at).exists():
                raise ValidationError('成就主单原付款时点待核对')
        for item in data['addons']:
            addon = OrderAddonConsumption.objects.select_related('payment__intent',
                'payment__buyer_transaction__wallet').filter(pk=item['consumption_id'],
                    account_id=event.account_id, business_cycle=event.business_cycle,
                    payment_id=item['payment_id'], payment__intent__order_id=item['order_id'],
                    payment__intent__order__is_gift_snapshot=False).first()
            if (not addon or addon.paid_units != item['paid'] or addon.confirmed_at > event.decided_at
                    or addon.payment.intent.buyer_id != event.account_id
                    or addon.payment.intent.order.business_cycle != event.business_cycle
                    or not addon.payment.intent.order.settled_at
                    or addon.payment.intent.order.settled_at > addon.confirmed_at
                    or addon.payment.paid_at > addon.confirmed_at
                    or addon.payment.buyer_transaction.created_at > event.decided_at):
                raise ValidationError('成就加购原实销时点待核对')
            actual = list(addon.payment.refund_records.filter(created_at__lte=event.decided_at).order_by('pk')
                          .values_list('pk', 'amount'))
            if [[pk, amount] for pk, amount in actual] != item['refunds']:
                raise ValidationError('成就加购原退款记录待核对')
            verify_receipt(addon.payment)
            ordinary_ids, segment_ids = item.get('settlements'), item.get('segment_settlements')
            if (not isinstance(ordinary_ids, list) or not isinstance(segment_ids, list)
                    or any(type(pk) is not int for pk in ordinary_ids + segment_ids)
                    or len(set(ordinary_ids)) != len(ordinary_ids)
                    or len(set(segment_ids)) != len(segment_ids)):
                raise ValidationError('成就加购原结算清单缺失')
            ordinary = list(addon.payment.settlements.filter(settled_at__lte=addon.confirmed_at)
                .select_related('segment__attribution', 'report', 'order_settlement',
                    'refund_settlement__posting__points_refund__redemption', 'income_transaction__wallet').order_by('pk'))
            segmented = list(addon.payment.segment_settlements.filter(settled_at__lte=addon.confirmed_at)
                .select_related('segment__attribution', 'report', 'manifest', 'income_transaction__wallet').order_by('pk'))
            if ([row.pk for row in ordinary] != ordinary_ids or [row.pk for row in segmented] != segment_ids
                    or (not ordinary and not segmented and item['paid'] > sum(row[1] for row in item['refunds']))):
                raise ValidationError('成就加购原收益结算清单不一致')
            for row in ordinary + segmented:
                if row.segment.attribution.order_id != item['order_id'] or row.settled_at > addon.confirmed_at:
                    raise ValidationError('成就加购收益归属待核对')
                if row in ordinary:
                    parent = (row.order_settlement.order_id if row.order_settlement_id else
                              row.refund_settlement.posting.points_refund.redemption.order_id)
                    if (parent != item['order_id'] or row.report.order_id != item['order_id']
                            or row.report.provider_account_id != row.segment.provider_account_id):
                        raise ValidationError('成就加购报单归属待核对')
                elif row.manifest.order_id != item['order_id'] or row.report.segment_id != row.segment_id:
                    raise ValidationError('成就转单加购报单归属待核对')
                tx = row.income_transaction
                if (row.amount and (not tx or tx.order_id != item['order_id']
                        or tx.wallet.account_id != row.segment.provider_account_id
                        or tx.tx_type != Transaction.TxType.INCOME or tx.status != Transaction.Status.SUCCESS
                        or tx.amount != row.amount or tx.balance_after != tx.balance_before + tx.amount
                        or tx.created_at > event.decided_at)) or (not row.amount and tx):
                    raise ValidationError('成就加购收益流水待核对')
        rules = data['rules']
        if (len({item['id'] for item in rules}) != len(rules)
                or any(set(item) != {'id', 'metric', 'target'} or type(item['id']) is not int
                       or item['metric'] not in ('amount', 'orders') or type(item['target']) is not int
                       or item['target'] < 0 for item in rules)):
            raise ValidationError('成就原规则清单异常')
        eligible = [item for item in rules if event.metrics['amount' if item['metric'] == 'amount' else 'count'] >= item['target']]
        if sorted(eligible, key=lambda x: x['id']) != sorted(event.eligible, key=lambda x: x['id']):
            raise ValidationError('成就原规则与达标记录不一致')
        return eligible
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError('成就原实销清单格式异常') from exc
