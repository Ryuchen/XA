"""Read-only, bounded screening of pending balance-order payment evidence."""
import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Exists, F, OuterRef, Q

from orders.direct_payment_models import DirectPaymentIntent
from orders.models import Order, OrderRefund
from wallet.models import Transaction


class Command(BaseCommand):
    help = '只读筛查已完成、待结算订单的余额付款来源；结果不是核账结论。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500, help='每批最多检查订单数（1–5000）')
        parser.add_argument('--after-id', type=int, default=0, help='上批 next_after_id；不是个人身份')
        parser.add_argument('--include-review-ids', action='store_true',
                            help='额外输出待复核内部订单 ID 和原因；须在权限受控环境处理')

    def handle(self, *args, **options):
        limit, after = options['limit'], options['after_id']
        if not 1 <= limit <= 5000 or after < 0:
            raise CommandError('limit 须为 1–5000，after-id 须不小于 0')
        # A bounded keyset page, not a consistent database snapshot. No locks,
        # creation, updates, wallet addressing, or personal data in output.
        rows = list(Order.objects.filter(pk__gt=after, status=Order.Status.COMPLETED,
            payment_status=Order.PaymentStatus.PAID, settled_at__isnull=True)
            .order_by('pk').values('id', 'amount', 'is_gift_snapshot',
                                   'service__service_category__is_gift')[:limit + 1])
        more = len(rows) > limit
        rows = rows[:limit]
        ids = [r['id'] for r in rows]
        direct = set(DirectPaymentIntent.objects.filter(order_id__in=ids).values_list('order_id', flat=True))
        refunds = set(OrderRefund.objects.filter(order_id__in=ids).values_list('order_id', flat=True))
        reversals = Transaction.objects.filter(reversal_of_id=OuterRef('pk'))
        pay = (Transaction.objects.filter(order_id__in=ids, tx_type=Transaction.TxType.PAY)
            .annotate(has_reversal=Exists(reversals)).values('order_id').annotate(
                count=Count('pk'),
                bad_amount=Count('pk', filter=~Q(amount=-F('order__amount'))),
                bad_user=Count('pk', filter=~Q(wallet__user_id=F('order__customer_id'))),
                bad_account=Count('pk', filter=Q(order__customer_account__isnull=False,
                    wallet__account__isnull=False) & ~Q(wallet__account_id=F('order__customer_account_id'))),
                missing_account=Count('pk', filter=Q(order__customer_account__isnull=False,
                    wallet__account__isnull=True)),
                bad_status=Count('pk', filter=~Q(status=Transaction.Status.SUCCESS)),
                bad_arithmetic=Count('pk', filter=~Q(balance_after=F('balance_before') + F('amount'))),
                reversed=Count('pk', filter=Q(reversal_of__isnull=False) | Q(has_reversal=True)),
            ))
        evidence = {r['order_id']: r for r in pay}
        counts = Counter({key: 0 for key in ('direct_excluded', 'gift_excluded', 'balance_candidates',
                                               'zero_amount', 'needs_review', 'no_issue_detected')})
        reasons = Counter({key: 0 for key in ('missing_pay', 'multiple_pay', 'amount_mismatch',
            'wallet_user_mismatch', 'wallet_account_mismatch', 'wallet_account_unverified', 'pay_not_success',
            'balance_arithmetic_mismatch', 'pay_reversal', 'refund_record_present')})
        review_orders = []
        for order in rows:
            pk = order['id']
            if pk in direct:
                counts['direct_excluded'] += 1
                continue
            if order['is_gift_snapshot'] or order['service__service_category__is_gift']:
                counts['gift_excluded'] += 1
                continue
            counts['balance_candidates'] += 1
            if order['amount'] == 0:
                counts['zero_amount'] += 1
            entry = evidence.get(pk)
            found = set()
            if not entry:
                found.add('missing_pay')
            else:
                if entry['count'] != 1:
                    found.add('multiple_pay')
                for key, label in (('bad_amount', 'amount_mismatch'), ('bad_user', 'wallet_user_mismatch'),
                        ('bad_account', 'wallet_account_mismatch'), ('missing_account', 'wallet_account_unverified'),
                        ('bad_status', 'pay_not_success'), ('bad_arithmetic', 'balance_arithmetic_mismatch'),
                        ('reversed', 'pay_reversal')):
                    if entry[key]:
                        found.add(label)
            if pk in refunds:
                found.add('refund_record_present')
            if found:
                counts['needs_review'] += 1
                reasons.update(found)
                if options['include_review_ids']:
                    review_orders.append({'order_id': pk, 'reasons': sorted(found)})
            else:
                counts['no_issue_detected'] += 1
        result = {
            'notice': '只读筛查，非一致性快照；并发变化可能影响统计，结果仅供人工核查，不证明资金真实或异常。',
            'scope': '已完成、付款状态 PAID、未结算的订单；直付及快照或当前分类为礼物的订单排除，未检查转单提前入账。原因可重叠，数量不能相加。',
            'scanned': len(rows), 'has_more': more, 'next_after_id': rows[-1]['id'] if rows else None,
            'counts': dict(counts), 'review_reasons': dict(reasons),
        }
        if options['include_review_ids']:
            result['review_orders'] = review_orders
            result['review_orders_notice'] = '订单 ID 与复核原因须在权限受控环境处理；该清单不是最终核账结论。'
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
