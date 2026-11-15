"""Cumulative refunds recover exact original successful credits, atomically."""
import hashlib
import json
from collections import defaultdict
from fractions import Fraction
from django.conf import settings
from django.core import signing
from stores.db import transaction
from stores.context import current_currency_name
from django.db.models import Sum,OuterRef,Subquery,F,Value,BigIntegerField,Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from club_accounts.models import ClubAccount
from console.permissions import get_account_permissions, is_console_account
from users.models import EscortProfile
from wallet.models import IncomeHold, Transaction, Wallet
from wallet.notifier import owner_account_id
from .models import Order, OrderRefund, RefundRecoveryLine
from .settlement import SettlementError

PLAN_SALT = 'orders.partial-refund.v2'
PAID_STATES = ('PAID','PARTIALLY_REFUNDED')

class RefundError(SettlementError):
    def __init__(self,message,*,code='REFUND_NOT_ALLOWED',data=None):
        super().__init__(message)
        self.code,self.data = code,data or {}

def authorized_refund(account):
    return bool(account and account.account_type == ClubAccount.AccountType.STAFF
                and is_console_account(account) and 'order:refund' in get_account_permissions(account))

def posted_settlement(order):
    return bool(order.settled_at or order.transactions.filter(status='SUCCESS',reversal_of__isnull=True,
                tx_type__in=['INCOME','GIFT','SHOP_INCOME'],amount__gt=0).exists()
                or order.providers.filter(settled_at__isnull=False,provider_income__gt=0).exists())

def refunded_amount(order):
    if 'refund_records' in getattr(order, '_prefetched_objects_cache', {}):
        return sum(row.amount for row in order.refund_records.all())
    return OrderRefund.objects.filter(order=order).aggregate(v=Sum('amount'))['v'] or 0

def with_net_amount(qs):
    sums = OrderRefund.objects.filter(order_id=OuterRef('pk')).order_by().values('order_id').annotate(v=Sum('amount')).values('v')[:1]
    return qs.annotate(_net_paid_amount=F('amount')-Coalesce(Subquery(sums,output_field=BigIntegerField()),Value(0),output_field=BigIntegerField()))

def parse_refund_amount(value,remaining):
    if value is None:
        return remaining
    if isinstance(value,str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or value <= 0 or value > remaining:
        raise RefundError('退款金额须为正整数且不能超过可退余款',code='REFUND_INVALID_AMOUNT',data={'remaining_amount':remaining})
    return value

def proportional_targets(weights,total):
    """Exact monotone midpoint prefix, without floats or per-money-unit loops."""
    weights = {key:value for key,value in weights.items() if value > 0}
    if not 0 <= total <= sum(weights.values()):
        raise RefundError('累计退款分摊金额异常',code='REFUND_LEDGER_INCOMPLETE')
    if not weights or total == 0:
        return {key:0 for key in weights}
    if total == sum(weights.values()):
        return dict(weights)
    grid = 2*max(weights.values())**2+1
    def counts(at):
        return {key:min(value,(2*value*at+grid)//(2*grid)) for key,value in weights.items()}
    lo,hi = 0,grid
    while lo < hi:
        mid = (lo+hi)//2
        if sum(counts(mid).values()) >= total:
            hi = mid
        else:
            lo = mid+1
    result = counts(lo-1)
    missing = total-sum(result.values())
    positions = sorted((Fraction(2*result[key]+1,2*value),key) for key,value in weights.items() if result[key] < value)
    if not 0 <= missing <= len(positions):
        raise RefundError('累计退款整数分摊异常',code='REFUND_LEDGER_INCOMPLETE')
    for _,key in positions[:missing]:
        result[key] += 1
    return result

def request_fingerprint(amount,reason,allocation=None):
    intent = [amount,reason] if allocation is None else [amount,reason,allocation]
    return hashlib.sha256(json.dumps(intent,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def replay_refund(order,request_id,amount,reason,allocation=None):
    if not request_id:
        return None
    if not isinstance(request_id,str) or not 1 <= len(request_id) <= 64:
        raise RefundError('退款操作凭证须为1至64字字符串',code='REFUND_INVALID_REQUEST')
    existing = OrderRefund.objects.filter(order=order,client_request_id=request_id).first()
    if existing:
        intended = parse_refund_amount(amount,order.amount) if amount is not None else existing.amount
        if existing.request_fingerprint != request_fingerprint(intended,reason,allocation):
            raise RefundError('同一操作凭证不能用于不同退款意图',code='REFUND_REQUEST_CONFLICT')
    return existing

def is_partially_settled_transfer(order):
    if not (order.payment_status == 'PAID' and order.amount > 0
            and order.settlement_mode == 'COMPLETION' and not order.settled_at):
        return False
    shares = list(order.providers.all())
    return (any(row.settled_at and row.provider_income for row in shares)
            and any(not row.settled_at for row in shares))


def negotiated_aftersale_blocks_completion(order):
    return (is_partially_settled_transfer(order)
            and any(row.status == 'OPEN' for row in order.aftersales.all()))


def can_request_aftersale(order):
    if (getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) and order.amount == 0 and order.status == 'IN_SERVICE'
            and order.payment_status == 'PAID'):
        from .models import OrderPlatformSubsidy
        if OrderPlatformSubsidy.objects.filter(order=order).exists():
            return not any(row.status in ('OPEN', 'REFUNDED') for row in order.aftersales.all())
    if order.status == 'IN_SERVICE' and is_partially_settled_transfer(order):
        return not any(row.status in ('OPEN', 'REFUNDED') for row in order.aftersales.all())
    if (order.status != 'COMPLETED' or order.payment_status not in PAID_STATES
            or (order.amount > 0 and refunded_amount(order) >= order.amount)):
        return False
    rows = list(order.aftersales.all())
    if rows and rows[0].status in ('OPEN','REFUNDED'):
        return False
    from engagement.confirmation import aftersale_deadline
    cutoff = aftersale_deadline(order)
    return bool(cutoff and timezone.now() < cutoff)

def _customer_wallet(order):
    rows = list(Wallet.objects.filter(user_id=order.customer_id)) if order.customer_id else []
    if order.customer_account_id:
        rows += list(Wallet.objects.filter(account_id=order.customer_account_id))
    rows = {row.pk:row for row in rows}
    if len(rows) != 1:
        raise RefundError('客户钱包寻址异常，无法退款')
    return next(iter(rows.values()))

def reject_zero_negotiation_refund(order):
    """Do not apply legacy recovery rules to a zero-termination receipt."""
    from rest_framework.exceptions import ValidationError
    from .negotiation_zero import marked_execution_id
    try:
        marker = marked_execution_id(order)
    except ValidationError as exc:
        raise RefundError('零退款协商凭证异常，请先核对', code='ZERO_NEGOTIATION_REVIEW') from exc
    if marker is not None:
        raise RefundError('零退款协商终止后的退款尚待核对，暂不能执行', code='ZERO_NEGOTIATION_REFUND_UNAVAILABLE')


def reject_additional_negotiation_refund(order):
    """Legacy refund accounting cannot compose with a prior negotiated receipt.

    Look up persisted rows, never a possibly prefetched/stale order.  A receipt
    also protects the order if its refund marker has been damaged.  Stores
    predating the execution table can still use their ordinary refund flow.
    """
    rows = list(OrderRefund.objects.filter(order_id=order.pk).values_list('pk', 'benefit_effects'))
    if not rows:
        return
    marked = any(isinstance(effects, dict) and effects.get('negotiation_execution_version') == 1
                 for _, effects in rows)
    if not marked:
        from .models import NegotiationExecution
        connection = transaction.get_connection()
        with connection.cursor() as cursor:
            has_table = NegotiationExecution._meta.db_table in connection.introspection.table_names(cursor)
        # exists() selects no new zero-refund columns, so this also works on
        # stores with the original execution table but without migration 0121.
        marked = has_table and NegotiationExecution.objects.filter(refund_id__in=[pk for pk, _ in rows]).exists()
    if marked:
        raise RefundError('协商退款后的追加退款尚待核对，暂不能执行',
                          code='NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE')


def build_refund_plan(order,*,refund_amount=None,lock=False,exclude_refund_id=None,negotiated=None,
                      funded_recovery=None):
    """GET stays read-only; confirmation recalculates with ordered locks."""
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        raise RefundError('积分订单退款处理尚未开放，请联系客服核对', code='POINTS_REFUND_UNAVAILABLE')
    reject_zero_negotiation_refund(order)
    reject_additional_negotiation_refund(order)
    if lock and not transaction.get_connection().in_atomic_block:
        raise RuntimeError('退款追缴必须处于订单加锁事务')
    from .segment_settlement import reject_legacy_refund
    reject_legacy_refund(order)
    if any(case.affected_provider_account_ids for case in order.aftersales.filter(status='OPEN')):
        raise RefundError('指定陪玩的售后退款分摊尚待核对，暂不能执行退款', code='REFUND_SCOPED_ALLOCATION_REQUIRED')
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        raise RefundError('微信支付订单的渠道退款尚未开放', code='REFUND_CHANNEL_UNSUPPORTED')
    persisted = Order.objects.values('status','payment_status','settled_at').get(pk=order.pk)
    prior_rows = list(OrderRefund.objects.filter(order=order).exclude(pk=exclude_refund_id).order_by('pk'))
    prior = sum(row.amount for row in prior_rows)
    if prior > order.amount:
        raise RefundError('累计退款超过原实付，请先人工核账',code='REFUND_LEDGER_INCOMPLETE')
    if funded_recovery is not None:
        if order.amount != 0 or prior or negotiated is not None:
            raise RefundError('安全箱补贴追回条件已变化',code='REFUND_LEDGER_INCOMPLETE')
        amount = 0
        negotiated = {'recovery': funded_recovery, 'fingerprint': sorted(funded_recovery.items())}
    else:
        amount = parse_refund_amount(refund_amount,order.amount-prior)
    if order.funding_terms is not None and order.amount:
        from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
        from engagement.funding_wallet import validate_order_funding_sources
        from rest_framework.exceptions import ValidationError
        allocations = list(order.funding_allocations.select_related('lot').order_by('pk'))
        if (not allocations or sum(row.amount for row in allocations) != order.amount
                or sum(row.refunded_amount for row in allocations) != prior):
            raise RefundError('订单资金来源或历史退款不完整，请先核对', code='REFUND_LEDGER_INCOMPLETE')
        try:
            validate_order_funding_sources(order, allocations)
            plan_refund([RefundablePart(row.pk, row.kind, row.amount, row.refunded_amount)
                         for row in allocations], amount)
        except (FundingPlanError, ValidationError) as exc:
            raise RefundError('订单资金来源或历史退款不一致，请先核对', code='REFUND_LEDGER_INCOMPLETE') from exc
    customer = _customer_wallet(order)
    if lock:
        customer = Wallet.objects.select_for_update().get(pk=customer.pk)
    originals = list(order.transactions.filter(status='SUCCESS',reversal_of__isnull=True).order_by('pk'))
    credits = [row for row in originals if row.tx_type in ('INCOME','GIFT','SHOP_INCOME') and row.amount > 0]
    settled = posted_settlement(order)
    if not settled and amount != order.amount-prior:
        raise RefundError('未结算订单仅支持退还全部余款',code='REFUND_PARTIAL_UNSETTLED')
    referral_funding = None
    referral_funding_amount = 0
    if settled:
        payments = [row for row in originals if row.tx_type == 'PAY' or (row.tx_type == 'GIFT' and row.amount < 0)]
        paid = -sum(row.amount for row in payments)
        if (paid != order.amount or any(row.wallet_id != customer.pk or row.amount > 0 for row in payments)
                or (negotiated is None and sum(row.amount for row in credits) != paid)):
            raise RefundError('原支付、成功结算入账与订单实付不一致，请先人工核账',code='REFUND_LEDGER_INCOMPLETE')
    reversals = list(order.transactions.filter(status='SUCCESS',reversal_of__isnull=False).order_by('pk'))
    reversed_totals = defaultdict(int)
    for row in reversals:
        reversed_totals[row.reversal_of_id] += -row.amount
    weights = {row.pk:row.amount for row in credits}
    if negotiated is not None:
        if not settled or prior or set(negotiated['recovery']) != set(weights):
            raise RefundError('协商退款原收益流水已变化，请重新核对',code='REFUND_LEDGER_INCOMPLETE')
        previous_targets = {pk:0 for pk in weights}
        targets = negotiated['recovery']
        if any(type(value) is not int or value < 0 or value > weights[pk] for pk,value in targets.items()):
            raise RefundError('协商追回金额与原收益不符',code='REFUND_LEDGER_INCOMPLETE')
    else:
        previous_targets = proportional_targets(weights,prior) if settled else {}
        targets = proportional_targets(weights,prior+amount) if settled else {}
    referral = []
    if settled:
        from .referral_evidence import paid_referral_credits, ReferralEvidenceError
        from .referral_refund_funding import verify_referral_history
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            referral = paid_referral_credits(order, credits, expected_amount=order.inviter_commission)
            if referral and prior_rows:
                verify_referral_history(order, credits, referral, prior_rows)
        except (ReferralEvidenceError, DjangoValidationError) as exc:
            raise RefundError(str(exc), code='REFUND_REFERRAL_EVIDENCE_REQUIRED') from exc
        pending_referral = list(order.referral_refund_funding.filter(refund__isnull=True,
                                                                      release__isnull=True).order_by('pk'))
        if pending_referral and (len(pending_referral) != 1 or pending_referral[0].target_refund_amount != amount):
            raise RefundError('已有其他退款金额的推荐佣金补资，请先核对',
                              code='REFUND_REFERRAL_STORE_FUNDING_REQUIRED')
    referral_ids = {row.pk for row in referral}
    if settled and any(reversed_totals[row.pk] != (0 if row.pk in referral_ids else previous_targets[row.pk])
                       for row in credits):
        raise RefundError('历史退款与原入账冲正不一致，请先人工核账',code='REFUND_LEDGER_INCOMPLETE')
    deltas = {row.pk:targets[row.pk]-previous_targets[row.pk] for row in credits}
    if settled:
        # Referral credits remain with their recipients, including when the
        # inviter and provider share a wallet. A verified separate store
        # receipt funds the corresponding proportional refund amount.
        if negotiated is not None and referral:
            raise RefundError('已发推荐佣金的协商退款尚待核对',
                              code='REFUND_REFERRAL_STORE_FUNDING_REQUIRED')
        if any(deltas[row.pk] for row in referral):
            from .referral_refund_funding import checked_allocation
            from django.core.exceptions import ValidationError as DjangoValidationError
            referral_funding_amount = sum(deltas[row.pk] for row in referral)
            try:
                referral_funding = checked_allocation(order, refund_amount=amount,
                    referral_amount=referral_funding_amount, lock=lock, exclude_refund_id=exclude_refund_id)
            except DjangoValidationError as exc:
                raise RefundError('已发推荐佣金的门店到账补资待核对',
                                  code='REFUND_REFERRAL_STORE_FUNDING_REQUIRED') from exc
            for row in referral:
                deltas[row.pk] = 0
    deposits = [row for row in originals if row.tx_type == 'DEPOSIT' and row.amount < 0]
    platform_deposits = [row for row in originals if row.tx_type == 'DEPOSIT_INCOME' and row.amount > 0]
    wallet_ids = {customer.pk}|{row.wallet_id for row in credits+deposits+platform_deposits}
    wallets = {row.pk:row for row in Wallet.objects.filter(pk__in=wallet_ids)}
    platform_ids = set(Wallet.objects.filter(pk__in=wallet_ids,user__username=settings.PLATFORM_SYSTEM_USERNAME).values_list('pk',flat=True))
    profile_ids = {row.user_id for row in wallets.values()}
    profile_ids.update(order.providers.exclude(provider_id=None).values_list('provider_id',flat=True))
    if order.provider_id:
        profile_ids.add(order.provider_id)
    profiles_qs = EscortProfile.objects.filter(user_id__in=profile_ids).order_by('user_id')
    profiles = {row.user_id:row for row in (profiles_qs.select_for_update() if lock else profiles_qs)}
    if lock:
        for pk in sorted(wallet_ids-platform_ids-{customer.pk}):
            wallets[pk] = Wallet.objects.select_for_update().get(pk=pk)
        for pk in sorted(platform_ids):
            wallets[pk] = Wallet.objects.select_for_update().get(pk=pk)
        wallets[customer.pk] = customer
    holds_qs = IncomeHold.objects.filter(income_transaction_id__in=weights).order_by('wallet_id','pk')
    holds = list(holds_qs.select_for_update() if lock else holds_qs)
    by_wallet = defaultdict(lambda:{'income':0,'held':0,'deposit':0,'debit':0,'ids':[],'safe':True})
    for credit in credits:
        if not deltas[credit.pk]:
            continue
        by_wallet[credit.wallet_id]['income'] += deltas[credit.pk]
        by_wallet[credit.wallet_id]['ids'].append(credit.pk)
    hold_freezes,used_freezes = [],set()
    for hold in holds:
        if not 0 <= hold.recovered_amount <= hold.amount:
            raise RefundError('该单收益冻结记录异常',code='REFUND_LEDGER_INCOMPLETE')
        if hold.released_at or hold.cancelled_at:
            continue
        remaining = hold.amount-hold.recovered_amount
        from wallet.income import valid_freeze_transaction
        if (not valid_freeze_transaction(hold) or hold.freeze_transaction_id in used_freezes
                or wallets[hold.wallet_id].frozen_amount < remaining):
            raise RefundError('该单冻结原流水或余额异常',code='REFUND_LEDGER_INCOMPLETE')
        freeze = hold.freeze_transaction
        used_freezes.add(freeze.pk)
        if -reversed_totals[freeze.pk] != hold.recovered_amount:
            raise RefundError('冻结已追回金额与冲正不一致',code='REFUND_LEDGER_INCOMPLETE')
        taken = min(deltas[hold.income_transaction_id],remaining)
        if taken:
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                wallet = wallets[hold.wallet_id]
                if (hold.income_transaction.wallet_id != wallet.pk
                        or hold.income_transaction.order_id != order.pk
                        or not wallet.account_id or not hold.account_business_cycle
                        or not ClubAccount.objects.filter(pk=wallet.account_id,
                            account_type=ClubAccount.AccountType.PROVIDER,
                            license_membership_active=True,
                            business_cycle=hold.account_business_cycle).exists()):
                    raise RefundError('冻结收益账号或来源周期待核对', code='REFUND_INCOME_CYCLE_REVIEW')
            hold_freezes.append((hold,freeze,taken))
            by_wallet[hold.wallet_id]['held'] += taken
    deposit_pairs,used_deposits = [],set()
    for debit in deposits:
        remaining = -debit.amount+reversed_totals[debit.pk]
        if not 0 <= remaining <= -debit.amount:
            raise RefundError('押金历史冲正异常',code='REFUND_LEDGER_INCOMPLETE')
        wallet,profile = wallets[debit.wallet_id],profiles.get(wallets[debit.wallet_id].user_id)
        ambiguous = Transaction.objects.filter(wallet_id=wallet.pk,tx_type='DEPOSIT',status='SUCCESS',
                    amount__gt=0,reversal_of__isnull=True,pk__gt=debit.pk).exists()
        safe = bool(profile and profile.deposit_paid >= remaining and not ambiguous)
        by_wallet[wallet.pk]['safe'] &= safe
        if not safe or not remaining:
            continue
        matches = [row for row in platform_deposits if row.pk > debit.pk and row.amount == -debit.amount
                   and row.pk not in used_deposits and row.wallet_id in platform_ids]
        if not matches:
            raise RefundError('收益抵扣押金缺少平台配对流水',code='REFUND_LEDGER_INCOMPLETE')
        credit = matches[0]
        used_deposits.add(credit.pk)
        if reversed_totals[credit.pk] != -reversed_totals[debit.pk]:
            raise RefundError('押金配对历史冲正不一致',code='REFUND_LEDGER_INCOMPLETE')
        item = by_wallet[wallet.pk]
        taken = min(remaining,max(0,item['income']-item['held']-item['deposit']))
        if taken:
            deposit_pairs.append((debit,credit,profile,taken))
            item['deposit'] += taken
            by_wallet[credit.wallet_id]['debit'] += taken
    deposit_pool = Transaction.objects.filter(tx_type='DEPOSIT_INCOME',status='SUCCESS',wallet_id__in=platform_ids).aggregate(v=Sum('amount'))['v'] or 0
    recoveries,shortfalls = [],[]
    for wallet_id,item in sorted(by_wallet.items()):
        wallet = wallets[wallet_id]
        required = item['income']+item['debit']-item['held']-item['deposit']
        if required < 0 or item['held'] > wallet.frozen_amount:
            raise RefundError('本次追回与同单冻结/押金不一致',code='REFUND_LEDGER_INCOMPLETE')
        protected = max(0,deposit_pool-item['debit']) if wallet_id in platform_ids else 0
        available = max(0,wallet.balance-protected)
        wallet_account = owner_account_id(wallet)
        is_inviter = ((order.inviter_account_id and wallet_account == order.inviter_account_id) or
                      (order.inviter_id and wallet.user_id == order.inviter_id))
        # A shared provider/inviter wallet can contain distinct credit rows.
        # Preserved referral credits must not label provider recovery as a
        # clawback of the referral commission.
        if is_inviter and any(row.wallet_id == wallet_id and row.pk not in referral_ids and deltas[row.pk] > 0
                              and row.tx_type != 'SHOP_INCOME' for row in credits):
            is_inviter = False
        row = {'wallet_id':wallet_id,'account_id':wallet_account,
               'role':'PLATFORM' if wallet_id in platform_ids else ('INVITER' if is_inviter else 'PROVIDER'),
               'income_amount':item['income'],'held_amount':item['held'],'deposit_amount':item['deposit'],
               'available_required':required,'available_balance':available,'protected_deposit_amount':protected,
               'shortfall':max(0,required-available),'original_transaction_ids':item['ids'],'source_safe':item['safe'],
               'explanation':'' if item['safe'] else '原抵扣后押金来源不明确；仅从可用余额追回，不动其他押金'}
        recoveries.append(row)
        if row['shortfall']:
            shortfalls.append({'wallet_id':wallet_id,'account_id':row['account_id'],'required':required,'available':available,'shortfall':row['shortfall']})
    valid = bool((amount or funded_recovery is not None) and persisted['payment_status'] in PAID_STATES and persisted['status'] in ('PENDING','GRABBED','IN_SERVICE','COMPLETED'))
    from engagement.confirmation import refund_within_aftersale_window
    valid &= refund_within_aftersale_window(order)
    if not settled:
        valid &= not order.consumption_confirmed_at
    facts = {'order':[order.pk,order.amount,order.customer_id,order.customer_account_id,order.provider_id,order.provider_account_id,
                     order.inviter_id,order.inviter_account_id,order.settlement_mode,order.business_cycle,str(order.consumption_confirmed_at),persisted],
             'amount':amount,'refunds':[(r.pk,r.amount,r.client_request_id) for r in prior_rows],
             'transactions':[(r.pk,r.wallet_id,r.tx_type,r.amount,r.reversal_of_id) for r in originals+reversals],
             'wallets':[(pk,r.balance,r.frozen_amount) for pk,r in sorted(wallets.items())],
             'profiles':[(pk,r.deposit_paid) for pk,r in sorted(profiles.items())],
             'holds':[(r.pk,r.amount,r.recovered_amount,str(r.released_at),str(r.cancelled_at),str(r.release_at)) for r in holds],
             'recoveries':recoveries}
    if negotiated is not None:
        facts['negotiated'] = negotiated['fingerprint']
    if referral_funding:
        facts['referral_funding'] = [referral_funding.pk, referral_funding.receipt_id,
                                     referral_funding.amount, referral_funding.request_key]
    fingerprint = hashlib.sha256(json.dumps(facts,sort_keys=True,default=str).encode()).hexdigest()
    public = {'order_id':order.pk,'is_settled':settled,'eligible':bool(valid and not shortfalls),
              'original_amount':order.amount,'refunded_amount':prior,'remaining_amount':order.amount-prior,
              'refund_amount':amount,'remaining_after_refund':order.amount-prior-amount,'full_refund':prior+amount == order.amount,
              'recovery_total':sum(deltas.values()),'referral_store_funding':referral_funding_amount,
              'recoveries':recoveries,'shortfalls':shortfalls,
              'plan_token':signing.dumps({'order_id':order.pk,'refund_amount':amount,'fingerprint':fingerprint},salt=PLAN_SALT,compress=True)}
    return public,{'wallets':wallets,'credits':credits,'deltas':deltas,'hold_freezes':hold_freezes,
                  'deposit_pairs':deposit_pairs,'fingerprint':fingerprint,'referral_funding':referral_funding}

def recover_settlement(order,refund,*,plan_token,operator,operator_account,negotiated=None,funded_recovery=None):
    if not authorized_refund(operator_account):
        raise RefundError('仅有退款权限的后台账号可追回已结算资金')
    preview,state = build_refund_plan(order,refund_amount=refund.amount,lock=True,exclude_refund_id=refund.pk,
                                      negotiated=negotiated,funded_recovery=funded_recovery)
    if preview['shortfalls']:
        raise RefundError('追回余额不足，本次退款未执行',code='REFUND_RECOVERY_INSUFFICIENT',data={'shortfalls':preview['shortfalls'],'preview':preview})
    try:
        token = signing.loads(plan_token or '',salt=PLAN_SALT,max_age=600)
    except signing.BadSignature:
        token = None
    if token != {'order_id':order.pk,'refund_amount':refund.amount,'fingerprint':state['fingerprint']}:
        raise RefundError('退款试算已变化或过期，请重新试算确认',code='REFUND_PLAN_CHANGED',data={'preview':preview})
    from wallet.refund_deposit import enabled as deficit_enabled, protection_enabled as deficit_protected
    if (deficit_protected() and state['deposit_pairs'] and (not deficit_enabled()
            or any(state['wallets'][debit.wallet_id].balance > 0
                   for debit, _credit, _profile, _taken in state['deposit_pairs']))):
        # The legacy allocator consumes deposit ahead of available wallet.
        # Do not record that old ordering as a compliant refund deficit.
        raise RefundError('旧退款方案未先使用陪玩可用余额，须按新顺序重新核对',
                          code='REFUND_DEPOSIT_ORDER_REVIEW')
    refund.plan_fingerprint = state['fingerprint']
    def reverse(original,amount,source, deposit_pair=None):
        wallet = state['wallets'][original.wallet_id]
        before = wallet.balance
        wallet.balance += amount
        if wallet.balance < 0:
            raise RefundError('追回余额不足，本次退款未执行',code='REFUND_RECOVERY_INSUFFICIENT')
        wallet.save(update_fields=['balance','frozen_amount'])
        row = Transaction.objects.create(wallet=wallet,order=order,tx_type=original.tx_type,amount=amount,
            deposit_pair=deposit_pair,
            deposit_owner_id=original.deposit_owner_id if source == 'DEPOSIT' else None,
            deposit_business_cycle=original.deposit_business_cycle if source == 'DEPOSIT' else None,
            reversal_of=original,balance_before=before,balance_after=wallet.balance,
            operator=operator,operator_account=operator_account,remark=f'订单退款冲回：{order.order_no}'[:255])
        RefundRecoveryLine.objects.create(refund=refund,original_transaction=original,reversal_transaction=row,wallet=wallet,source=source,amount=amount)
        return row
    for debit,credit,profile,taken in state['deposit_pairs']:
        from wallet.deposit_cycle import deposit_cycle
        try:
            deposit_cycle(profile)
        except ValueError as exc:
            raise RefundError(str(exc), code='REFUND_DEPOSIT_CYCLE_REVIEW') from exc
        if (debit.deposit_owner_id != profile.account_id or credit.deposit_owner_id != profile.account_id
                or debit.deposit_business_cycle != profile.deposit_business_cycle
                or credit.deposit_business_cycle != profile.deposit_business_cycle):
            raise RefundError('押金流水账号周期待核对', code='REFUND_DEPOSIT_CYCLE_REVIEW')
        provider_reversal = reverse(debit,taken,'DEPOSIT')
        store_reversal = reverse(credit,-taken,'DEPOSIT', deposit_pair=provider_reversal)
        profile.deposit_paid -= taken
        profile.save(update_fields=['deposit_paid'])
        if deficit_enabled():
            from wallet.refund_deposit import register_refund_deficit
            register_refund_deficit(refund=refund, profile=profile, provider_transaction=provider_reversal,
                                    store_transaction=store_reversal)
    for hold,freeze,taken in state['hold_freezes']:
        state['wallets'][hold.wallet_id].frozen_amount -= taken
        reverse(freeze,taken,'HOLD')
        hold.recovered_amount += taken
        if hold.recovered_amount == hold.amount:
            hold.cancelled_at,hold.cancelled_by_refund = timezone.now(),refund
        hold.save(update_fields=['recovered_amount','cancelled_at','cancelled_by_refund'])
    for credit in state['credits']:
        delta = state['deltas'][credit.pk]
        if delta:
            reverse(credit,-delta,'COMMISSION' if credit.tx_type == 'SHOP_INCOME' else 'INCOME')
    if state['referral_funding']:
        funding = state['referral_funding']
        funding.refund = refund
        funding.save(update_fields=['refund'])
        from users.receipt_usage_sources import verify_allocations_locked
        verify_allocations_locked(funding.receipt_id)
    refund.recovered_amount = preview['recovery_total']
    return list(state['wallets'].values())

def schedule_refund_notices(order, amount, *, partial):
    """Tell offline parties about a committed balance refund once per operation."""
    from .services import push_message_safe

    whole, fraction = divmod(amount, 10)
    amount_text = f'{whole}.{fraction}' if fraction else str(whole)
    title = '订单部分退款' if partial else '订单已退款'
    terminated = partial and order.status == Order.Status.CANCELLED
    customer_prefix = '已终止，' if terminated else ''
    recipients = [(order.customer_id, order.customer_account_id,
                   f'订单「{order.order_no}」{customer_prefix}已退还 {amount_text} {current_currency_name()}至余额，可查看订单和钱包流水。', True)]
    shares = list(order.providers.filter(
        Q(released_at__isnull=True) | Q(settlement_base__gt=0) | Q(provider_income__gt=0)
    ).values_list('provider_id', 'provider_account_id'))
    if not shares and order.provider_id:
        shares = [(order.provider_id, order.provider_account_id)]
    latest = order.refund_records.order_by('-pk').first()
    inviter_owner = Q(wallet__account_id=order.inviter_account_id) if order.inviter_account_id else Q(wallet__user_id=order.inviter_id)
    referral_recovered = bool((order.inviter_id or order.inviter_account_id) and latest and latest.recovery_lines.filter(
            Q(original_transaction__remark__startswith='推荐分佣：') |
            Q(original_transaction__remark__startswith='礼物推荐分佣：'),
            inviter_owner).exists())
    referral_negotiated = bool(latest and any(row.get('role') == 'INVITER' and row.get('entitlement', 0) > 0
                                              for row in latest.negotiated_allocation))
    referral_affected = referral_recovered or referral_negotiated
    for user_id, account_id in shares:
        is_inviter = referral_affected and (
            (account_id and account_id == order.inviter_account_id) or
            (user_id and user_id == order.inviter_id))
        income = '收益（含推荐收益）' if is_inviter else '收益'
        status_text = '已终止并部分退款' if terminated else '已部分退款' if partial else '已退款'
        recipients.append((user_id, account_id,
            f'订单「{order.order_no}」{status_text}，{income}变化请查看钱包流水。', True))

    # A distinct inviter may receive a retained negotiated share or a reversal
    # without being a service participant; route them to their own wallet.
    if referral_affected:
        recipients.append((order.inviter_id, order.inviter_account_id,
                           f'订单「{order.order_no}」退款，推荐收益变化请查看钱包流水。', False))
    from site_messages.order_refund_notice import enabled as refund_notice_enabled, prepare as prepare_refund_notice
    from .points_service_payment import needs_integrated_processing
    from .direct_payment_models import DirectPaymentIntent
    recovery = (refund_notice_enabled() and not needs_integrated_processing(order)
                and not DirectPaymentIntent.objects.filter(order=order).exists())
    source = prepare_refund_notice(order, latest, amount) if recovery else None
    seen_users, seen_accounts = set(), set()
    for user_id, account_id, preview, can_open_order in recipients:
        if (not user_id and not account_id) or (user_id and user_id in seen_users) or (account_id and account_id in seen_accounts):
            continue
        if user_id:
            seen_users.add(user_id)
        if account_id:
            seen_accounts.add(account_id)
        from site_messages.order_cycle import recipient_cycle
        cycle = recipient_cycle(order, account_id)
        if recovery:
            from site_messages.order_refund_notice import record
            record(source, account_id=account_id, legacy_user_id=user_id, cycle=cycle, title=title,
                   preview=preview, can_open_order=can_open_order)
        else:
            transaction.on_commit(lambda user_id=user_id, account_id=account_id, preview=preview, can_open_order=can_open_order, cycle=cycle:
                push_message_safe(recipient_id=user_id, recipient_account_id=account_id, title=title,
                                  preview=preview, msg_type='ORDER', related_order_id=order.pk if can_open_order else None,
                                  action_url='' if can_open_order else '/pages/wallet/index',
                                  recipient_business_cycle=cycle, require_cycle=True))


@transaction.atomic
def apply_admin_refund(order_id,*,reason,operator,operator_account,refund_amount=None,client_request_id=None,plan_token=None,notify=True):
    from .models import Reservation
    from .points_service_payment import needs_integrated_processing
    candidate = Order.objects.filter(pk=order_id).first()
    formal_assigned = bool(getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
        and candidate and candidate.status == Order.Status.GRABBED
        and candidate.payment_status == Order.PaymentStatus.PAID
        and needs_integrated_processing(candidate))
    if formal_assigned:
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        from .points_business_access import service_order
        with service_order(order_id, phase='assignment'):
            return _apply_admin_refund(order_id, reason=reason, operator=operator, operator_account=operator_account,
                refund_amount=refund_amount, client_request_id=client_request_id, plan_token=plan_token, notify=notify)
    return _apply_admin_refund(order_id, reason=reason, operator=operator, operator_account=operator_account,
        refund_amount=refund_amount, client_request_id=client_request_id, plan_token=plan_token, notify=notify)


def _apply_admin_refund(order_id,*,reason,operator,operator_account,refund_amount=None,client_request_id=None,plan_token=None,notify=True):
    from .services import refund_to_customer,refresh_escort_status
    from .state_machine import transition,log_only
    from .notifier import notify_order_update
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    order = Order.objects.select_for_update().get(pk=order_id)
    if not authorized_refund(operator_account):
        raise RefundError('仅有退款权限的后台账号可操作退款')
    from .models import OrderPlatformSubsidy
    zero_subsidy = bool(order.amount == 0 and OrderPlatformSubsidy.objects.filter(order=order).exists())
    if zero_subsidy and (refund_amount is None or type(refund_amount) is int and refund_amount == 0
                         or refund_amount == '0'):
        refund_amount = None
    replayed = replay_refund(order,client_request_id,refund_amount,reason)
    if replayed:
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .referral_refund_funding import verify_replay
        try:
            verify_replay(replayed)
        except DjangoValidationError as exc:
            raise RefundError('推荐佣金退款凭据不一致，请先核对', code='REFUND_LEDGER_INCOMPLETE') from exc
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from rest_framework.exceptions import ValidationError
            from .points_service_refund import verify_refund_history_locked
            try:
                from .points_prestart_cancel_funding import verify_replay_locked as verify_cancel_funding_replay
                verify_cancel_funding_replay(replayed)
                verify_refund_history_locked(replayed.pk)
            except (ValidationError, DjangoValidationError) as exc:
                raise RefundError('积分退款凭据不一致，请先核对', code='REFUND_LEDGER_INCOMPLETE') from exc
        order._refund_replayed = True
        return order
    if order.payment_status not in PAID_STATES or order.status == 'CANCELLED':
        raise RefundError('订单当前没有可退余额')
    if zero_subsidy and order.status == Order.Status.GRABBED:
        if refund_amount is not None or plan_token:
            raise RefundError('安全箱服务前取消不产生钱包退款，请核对原券')
        from .subsidy_prestart_cancel import capture_locked, finish_locked
        facts = capture_locked(order)
        def cancel_safety_box(locked):
            refund = refund_to_customer(locked, reason=reason, operator=operator, operator_account=operator_account,
                refund_amount=None, client_request_id=client_request_id)
            finish_locked(locked, refund, facts)
            locked.cancel_reason = reason
        order = transition(order.pk, Order.Status.CANCELLED, operator=operator, operator_account=operator_account,
            action='REFUND', reason=reason, side_effect=cancel_safety_box,
            update_fields=['cancel_reason', 'payment_status', 'refunded_at'])
        refresh_escort_status(provider_ids=[order.provider_id, *order.providers.values_list('provider_id', flat=True)],
            account_ids=[order.provider_account_id, *order.providers.values_list('provider_account_id', flat=True)],
            exclude_order_ids=[order.pk])
        from .services import push_message_safe
        transaction.on_commit(lambda: push_message_safe(recipient_id=order.customer_id,
            recipient_account_id=order.customer_account_id, recipient_business_cycle=order.business_cycle,
            require_cycle=True, title='订单已取消', preview='订单已取消，原安全箱券已退回。',
            msg_type='ORDER', related_order_id=order.pk))
        if facts['provider_cycle']:
            transaction.on_commit(lambda: push_message_safe(recipient_id=order.provider_id,
                recipient_account_id=order.provider_account_id, recipient_business_cycle=facts['provider_cycle'],
                require_cycle=True, title='订单已取消', preview='服务开始前订单已取消，本单不产生补贴收益。',
                msg_type='ORDER', related_order_id=order.pk))
        if notify:
            transaction.on_commit(lambda: notify_order_update(order))
        return order
    amount = parse_refund_amount(refund_amount,order.amount-refunded_amount(order))
    from .points_service_payment import needs_integrated_processing
    from .points_business_access import permits as points_permits
    from .models import Reservation
    if (needs_integrated_processing(order)
            and Reservation.objects.filter(order=order, status=Reservation.Status.CONVERTED).exists()
            and (points_permits(order, phase='assignment', kind='service')
                 or getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_TESTING', False))):
        if amount != order.amount or plan_token:
            raise RefundError('预约未开始时仅支持按原支付构成全额退款')
        from .points_reserved_cancel_test import refund_locked
        def effect(locked):
            refund_locked(locked, reason=reason, operator=operator, operator_account=operator_account,
                          request_id=client_request_id)
            locked.cancel_reason = reason
        order = transition(order.pk, Order.Status.CANCELLED, operator=operator, operator_account=operator_account,
            action='REFUND', reason=reason, side_effect=effect,
            update_fields=['cancel_reason', 'payment_status', 'refunded_at'])
        evidence_id = getattr(order, '_reservation_cancel_funding_evidence_id', None)
        if evidence_id:
            from django.db import DatabaseError
            from rest_framework.exceptions import ValidationError
            from .models import ReservationPointsCancelFundingEvidence
            from .reservation_cancel_funding_test import verify_locked
            try:
                verify_locked(ReservationPointsCancelFundingEvidence.objects.get(pk=evidence_id))
            except (ValidationError, DatabaseError) as exc:
                raise RefundError('预约退款补资释放凭证待核对') from exc
        refresh_escort_status(provider_ids=[order.provider_id, *order.providers.values_list('provider_id', flat=True)],
            account_ids=[order.provider_account_id, *order.providers.values_list('provider_account_id', flat=True)],
            exclude_order_ids=[order.pk])
        schedule_refund_notices(order, amount, partial=False)
        if notify:
            transaction.on_commit(lambda: notify_order_update(order))
        return order
    if (needs_integrated_processing(order) and order.status == Order.Status.GRABBED
            and (points_permits(order, phase='assignment', kind='service')
                 or getattr(settings, 'SERVICE_POINTS_ASSIGNED_CANCEL_ENABLED', False))):
        if amount != order.amount or plan_token:
            raise RefundError('未开始的积分订单仅支持按原支付构成全额退款')
        from .points_assigned_cancel import refund_locked
        def effect(locked):
            refund_locked(locked, reason=reason, operator=operator, operator_account=operator_account,
                          request_id=client_request_id)
            locked.cancel_reason = reason
        order = transition(order.pk, Order.Status.CANCELLED, operator=operator, operator_account=operator_account,
            action='REFUND', reason=reason, side_effect=effect,
            update_fields=['cancel_reason', 'payment_status', 'refunded_at'])
        refresh_escort_status(provider_ids=[order.provider_id, *order.providers.values_list('provider_id', flat=True)],
            account_ids=[order.provider_account_id, *order.providers.values_list('provider_account_id', flat=True)],
            exclude_order_ids=[order.pk])
        schedule_refund_notices(order, amount, partial=False)
        if notify:
            transaction.on_commit(lambda: notify_order_update(order))
        return order
    partial = amount < order.amount-refunded_amount(order)
    kwargs = dict(reason=reason,operator=operator,operator_account=operator_account,allow_settled_recovery=True,
                  plan_token=plan_token,refund_amount=amount,client_request_id=client_request_id)
    if partial:
        if order.status != 'COMPLETED' or not posted_settlement(order):
            raise RefundError('仅已结算完成订单支持部分退款',code='REFUND_PARTIAL_UNSETTLED')
        refund_to_customer(order,**kwargs)
        order.save(update_fields=['payment_status','refunded_at','updated_at'])
        log_only(order,action='REFUND',operator=operator,operator_account=operator_account,reason=reason)
    else:
        def effect(locked):
            refund_to_customer(locked,**kwargs)
            locked.cancel_reason = reason
        order = transition(order.pk,'CANCELLED',operator=operator,operator_account=operator_account,action='REFUND',
            reason=reason,side_effect=effect,allow_settled_refund=True,
            update_fields=['cancel_reason','payment_status','refunded_at'])
        refresh_escort_status(provider_ids=[order.provider_id,*order.providers.values_list('provider_id',flat=True)],
            account_ids=[order.provider_account_id,*order.providers.values_list('provider_account_id',flat=True)],exclude_order_ids=[order.pk])
    schedule_refund_notices(order, amount, partial=partial)
    if notify:
        transaction.on_commit(lambda:notify_order_update(order))
    return order


def prepare_provider_refund_evidence(orders, account):
    """Batch the requesting provider's refund evidence for a list response."""
    if not account or account.account_type != 'PROVIDER':
        return
    from django.db.models import Prefetch, Q, prefetch_related_objects
    from club_accounts.models import LegacyAccountMap
    from .models import RefundRecoveryLine

    orders = [order for order in orders if order and list(order.refund_records.all())]
    for start in range(0, len(orders), 200):
        chunk = orders[start:start + 200]
        recoveries = RefundRecoveryLine.objects.select_related('wallet', 'reversal_transaction', 'original_transaction')
        prefetch_related_objects(chunk, Prefetch('refund_records__recovery_lines', queryset=recoveries))
        ids = [order.pk for order in chunk]
        legacy_user = LegacyAccountMap.objects.filter(account_id=account.pk).values('legacy_user_id')
        wallets = list(Wallet.objects.filter(Q(account_id=account.pk) |
            Q(account_id__isnull=True, user_id__in=legacy_user)))
        by_id = {wallet.pk: wallet for wallet in wallets}
        owned_by_order = defaultdict(set)
        for order_id, wallet_id in Transaction.objects.filter(order_id__in=ids, wallet_id__in=by_id).values_list('order_id', 'wallet_id').distinct():
            owned_by_order[order_id].add(wallet_id)
        incomes = defaultdict(list)
        for tx in Transaction.objects.filter(order_id__in=ids, wallet_id__in=by_id,
                status='SUCCESS', tx_type__in=['INCOME', 'GIFT']).select_related('reversal_of'):
            incomes[tx.order_id].append(tx)
        frozen = defaultdict(int)
        for hold in IncomeHold.objects.filter(wallet_id__in=by_id, income_transaction__order_id__in=ids,
                released_at__isnull=True, cancelled_at__isnull=True).values('wallet_id', 'income_transaction__order_id', 'amount', 'recovered_amount'):
            order_id = hold['income_transaction__order_id']
            if hold['wallet_id'] in owned_by_order[order_id]:
                frozen[order_id] += hold['amount'] - hold['recovered_amount']
        for order in chunk:
            order._provider_refund_evidence = {
                'account_id': account.pk, 'wallet_ids': set(by_id),
                'wallets': [by_id[pk] for pk in owned_by_order[order.pk]],
                'income': incomes[order.pk], 'frozen': frozen[order.pk],
            }


def refund_summary(order,*,detailed=False,account=None,customer_owner=False,provider_owner=False,include_coupon_results=False):
    rows = sorted(order.refund_records.all(), key=lambda row: (row.created_at, row.pk), reverse=True)
    provider = bool(account and account.account_type == 'PROVIDER')
    hidden_scoped_refunds = False
    if provider and not detailed:
        # A scoped negotiation is private to the affected participant.  The
        # other teammate may still see their own service and settlement, but
        # not the customer's refund amount or the other person's reason.
        marked = [row.pk for row in rows if isinstance(row.benefit_effects, dict)
                  and row.benefit_effects.get('negotiation_execution_version') == 1]
        if marked:
            from .models import NegotiationExecutionShare
            own_refunds = set(NegotiationExecutionShare.objects.filter(
                execution__refund_id__in=marked, account_id=account.pk).values_list('execution__refund_id', flat=True))
            hidden_scoped_refunds = any(pk not in own_refunds for pk in marked)
            rows = [row for row in rows if row.pk not in marked or row.pk in own_refunds]
        # Points negotiations use distinct posting evidence.  Never expose
        # another teammate's reason or amount through the generic summary.
        from .points_service_payment import needs_integrated_processing
        if rows and needs_integrated_processing(order):
            from .models import PointsNegotiationPosting, PointsNegotiationPostingShare
            from stores.db import connection
            with connection.cursor() as cursor:
                tables = set(connection.introspection.table_names(cursor))
            required = {PointsNegotiationPosting._meta.db_table, PointsNegotiationPostingShare._meta.db_table}
            own = set()
            if required.issubset(tables):
                own = set(PointsNegotiationPostingShare.objects.filter(account_id=account.pk,
                    posting__points_refund__refund_id__in=[row.pk for row in rows])
                    .values_list('posting__points_refund__refund_id', flat=True))
            hidden_scoped_refunds = hidden_scoped_refunds or any(row.pk not in own for row in rows)
            rows = [row for row in rows if row.pk in own]
    if not rows:
        return None
    evidence = getattr(order, '_provider_refund_evidence', None)
    if evidence and (not provider or evidence['account_id'] != account.pk):
        evidence = None
    records,all_lines = [],[]
    funding_by_refund = {}
    if detailed:
        from .models import ReferralRefundFunding
        funding_by_refund = {item.refund_id: item for item in ReferralRefundFunding.objects.filter(refund__in=rows)}
    legacy_provider_ids = set()
    if provider and any(row.negotiated_allocation for row in rows):
        from club_accounts.models import LegacyAccountMap
        legacy_provider_ids = set(LegacyAccountMap.objects.filter(account_id=account.pk).values_list('legacy_user_id', flat=True))
    for row in rows:
        item = {'id':row.pk,'amount':row.amount,'created_at':row.created_at,'reason':row.reason,'client_request_id':row.client_request_id}
        if include_coupon_results or (account and account.account_type == 'BOSS' and (account.pk == order.customer_account_id or customer_owner)):
            # Only events explicitly recorded at refund time are exposed;
            # old refunds have no reliable coupon-to-order link after restoration.
            if 'order_coupon_results' in row.benefit_effects:
                item['coupon_results'] = row.benefit_effects['order_coupon_results']
            if row.benefit_effects.get('subsidy_negotiated'):
                item['safety_box_replenishment'] = 'SETTLED' if order.settled_at else 'WAITING_SETTLEMENT'
            if row.benefit_effects.get('gift_claim_results'):
                item['gift_claim_results'] = row.benefit_effects['gift_claim_results']
            if row.benefit_effects.get('gift_rights_preserved'):
                item['gift_rights_preserved'] = True
        naming = row.benefit_effects.get('gift_naming')
        if naming and account and (account.pk in (order.customer_account_id, order.provider_account_id) or customer_owner or provider_owner):
            item['gift_naming_result'] = {'expires_at': naming['expires_at']}
            if naming.get('preserved'):
                item['gift_naming_result']['preserved'] = True
            else:
                item['gift_naming_result']['remaining_paid_grants'] = naming['remaining_paid_grants']
        lines = []
        if detailed or provider:
            recovery_lines = row.recovery_lines.all()
            if 'recovery_lines' not in getattr(row, '_prefetched_objects_cache', {}):
                recovery_lines = recovery_lines.select_related('wallet', 'reversal_transaction', 'original_transaction')
            lines = list(recovery_lines)
        if detailed:
            item.update(recovered_amount=row.recovered_amount,benefit_effects=row.benefit_effects,
                negotiated_allocation=row.negotiated_allocation,
                recovery_lines=[{'original_transaction_id':r.original_transaction_id,'reversal_transaction_id':r.reversal_transaction_id,
                'wallet_id':r.wallet_id,'account_id':owner_account_id(r.wallet),'tx_type':r.reversal_transaction.tx_type,'amount':r.amount,'source':r.source} for r in lines])
            funding = funding_by_refund.get(row.pk)
            item['referral_store_funding'] = ({'amount': funding.amount, 'allocation_id': funding.pk,
                'receipt_id': funding.receipt_id} if funding else None)
            all_lines.extend(item['recovery_lines'])
        elif provider:
            own_income = [r for r in lines if r.reversal_transaction.tx_type in ('INCOME','GIFT') and
                          (r.wallet_id in evidence['wallet_ids'] if evidence is not None else owner_account_id(r.wallet) == account.pk)]
            item['recovered_amount'] = sum(-r.amount for r in own_income if not r.original_transaction.remark.startswith(('推荐分佣：', '礼物推荐分佣：')))
            item['referral_recovered_amount'] = sum(-r.amount for r in own_income if r.original_transaction.remark.startswith(('推荐分佣：', '礼物推荐分佣：')))
            own_allocations = [allocation for allocation in row.negotiated_allocation
                if allocation.get('role') == 'PROVIDER' and
                (allocation.get('account_id') == account.pk or
                 (allocation.get('account_id') is None and allocation.get('user_id') in legacy_provider_ids))]
            if own_allocations:
                item['negotiated_provider_burden'] = sum(allocation['amount'] for allocation in own_allocations)
                item['negotiated_provider_retained'] = sum(allocation['retained'] for allocation in own_allocations)
        records.append(item)
    total = sum(r.amount for r in rows)
    data = {'id':rows[0].pk,'amount':total,'original_amount':order.amount,
            'remaining_amount':None if hidden_scoped_refunds else max(0,order.amount-total),
            'refund_count':len(rows),'created_at':rows[0].created_at,'reason':rows[0].reason,'records':records}
    if detailed:
        data.update(recovered_amount=sum(r.recovered_amount for r in rows),recovery_lines=all_lines,benefit_effects=rows[0].benefit_effects)
    elif provider:
        wallets = evidence['wallets'] if evidence is not None else [r for r in Wallet.objects.filter(pk__in=order.transactions.values('wallet_id')) if owner_account_id(r) == account.pk]
        owned = [r.pk for r in wallets]
        data['recovered_amount'] = sum(r['recovered_amount'] for r in records)
        data['referral_recovered_amount'] = sum(r['referral_recovered_amount'] for r in records)
        own_negotiated = [r for r in records if 'negotiated_provider_burden' in r]
        if own_negotiated:
            data['negotiated_provider_burden'] = sum(r['negotiated_provider_burden'] for r in own_negotiated)
            data['negotiated_provider_retained'] = sum(r['negotiated_provider_retained'] for r in own_negotiated)
        income = evidence['income'] if evidence is not None else list(order.transactions.filter(wallet_id__in=owned,status='SUCCESS',tx_type__in=['INCOME','GIFT']).select_related('reversal_of'))
        def referral(tx):
            original = tx.reversal_of if tx.reversal_of_id else tx
            return original.remark.startswith(('推荐分佣：', '礼物推荐分佣：'))
        data['net_income'] = sum(tx.amount for tx in income if not referral(tx))
        data['referral_net_income'] = sum(tx.amount for tx in income if referral(tx))
        # A historic unlabelled referral in the same wallet cannot be
        # distinguished from service pay by amount alone.
        same_inviter = any((order.inviter_account_id and wallet.account_id == order.inviter_account_id) or
                           (order.inviter_id and wallet.user_id == order.inviter_id) for wallet in wallets)
        recorded_referral = sum(tx.amount for tx in income if not tx.reversal_of_id and referral(tx))
        negotiated_referral = next((item for refund in rows for item in refund.negotiated_allocation
                                    if item.get('role') == 'INVITER'), None)
        expected_referral = negotiated_referral['retained'] if negotiated_referral else order.inviter_commission
        if same_inviter and expected_referral and recorded_referral != expected_referral:
            for key in ('recovered_amount', 'referral_recovered_amount', 'net_income', 'referral_net_income'):
                data[key] = None
            for item in records:
                item['recovered_amount'] = item['referral_recovered_amount'] = None
            data['income_breakdown_unavailable'] = True
        data['remaining_frozen_amount'] = evidence['frozen'] if evidence is not None else sum(r.amount-r.recovered_amount for r in IncomeHold.objects.filter(
            wallet_id__in=owned,income_transaction__order=order,released_at__isnull=True,cancelled_at__isnull=True))
    return data
