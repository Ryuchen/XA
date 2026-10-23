"""Cumulative refunds recover exact original successful credits, atomically."""
import hashlib
import json
from collections import defaultdict
from fractions import Fraction
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Sum,OuterRef,Subquery,F,Value,BigIntegerField
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

def request_fingerprint(amount,reason):
    return hashlib.sha256(json.dumps([amount,reason],ensure_ascii=False).encode()).hexdigest()

def replay_refund(order,request_id,amount,reason):
    if not request_id:
        return None
    if not isinstance(request_id,str) or not 1 <= len(request_id) <= 64:
        raise RefundError('退款操作凭证须为1至64字字符串',code='REFUND_INVALID_REQUEST')
    existing = OrderRefund.objects.filter(order=order,client_request_id=request_id).first()
    if existing:
        intended = parse_refund_amount(amount,order.amount) if amount is not None else existing.amount
        if existing.request_fingerprint != request_fingerprint(intended,reason):
            raise RefundError('同一操作凭证不能用于不同退款意图',code='REFUND_REQUEST_CONFLICT')
    return existing

def can_request_aftersale(order):
    if order.status != 'COMPLETED' or order.payment_status not in PAID_STATES or refunded_amount(order) >= order.amount:
        return False
    rows = list(order.aftersales.all())
    if rows and rows[0].status in ('OPEN','REFUNDED'):
        return False
    if posted_settlement(order):
        return True
    from engagement.confirmation import aftersale_deadline
    cutoff = aftersale_deadline(order)
    return bool(not rows and cutoff and timezone.now() < cutoff and not order.consumption_confirmed_at)

def _customer_wallet(order):
    rows = list(Wallet.objects.filter(user_id=order.customer_id))
    if order.customer_account_id:
        rows += list(Wallet.objects.filter(account_id=order.customer_account_id))
    rows = {row.pk:row for row in rows}
    if len(rows) != 1:
        raise RefundError('客户钱包寻址异常，无法退款')
    return next(iter(rows.values()))

def build_refund_plan(order,*,refund_amount=None,lock=False,exclude_refund_id=None):
    """GET stays read-only; confirmation recalculates with ordered locks."""
    if lock and not transaction.get_connection().in_atomic_block:
        raise RuntimeError('退款追缴必须处于订单加锁事务')
    persisted = Order.objects.values('status','payment_status','settled_at').get(pk=order.pk)
    prior_rows = list(OrderRefund.objects.filter(order=order).exclude(pk=exclude_refund_id).order_by('pk'))
    prior = sum(row.amount for row in prior_rows)
    if prior > order.amount:
        raise RefundError('累计退款超过原实付，请先人工核账',code='REFUND_LEDGER_INCOMPLETE')
    amount = parse_refund_amount(refund_amount,order.amount-prior)
    customer = _customer_wallet(order)
    if lock:
        customer = Wallet.objects.select_for_update().get(pk=customer.pk)
    originals = list(order.transactions.filter(status='SUCCESS',reversal_of__isnull=True).order_by('pk'))
    credits = [row for row in originals if row.tx_type in ('INCOME','GIFT','SHOP_INCOME') and row.amount > 0]
    settled = posted_settlement(order)
    if not settled and amount != order.amount-prior:
        raise RefundError('未结算订单仅支持退还全部余款',code='REFUND_PARTIAL_UNSETTLED')
    if settled:
        payments = [row for row in originals if row.tx_type == 'PAY' or (row.tx_type == 'GIFT' and row.amount < 0)]
        paid = -sum(row.amount for row in payments)
        if (paid != order.amount or any(row.wallet_id != customer.pk or row.amount > 0 for row in payments)
                or sum(row.amount for row in credits) != paid):
            raise RefundError('原支付、成功结算入账与订单实付不一致，请先人工核账',code='REFUND_LEDGER_INCOMPLETE')
    reversals = list(order.transactions.filter(status='SUCCESS',reversal_of__isnull=False).order_by('pk'))
    reversed_totals = defaultdict(int)
    for row in reversals:
        reversed_totals[row.reversal_of_id] += -row.amount
    weights = {row.pk:row.amount for row in credits}
    previous_targets = proportional_targets(weights,prior) if settled else {}
    targets = proportional_targets(weights,prior+amount) if settled else {}
    if settled and any(reversed_totals[row.pk] != previous_targets[row.pk] for row in credits):
        raise RefundError('历史退款与原入账冲正不一致，请先人工核账',code='REFUND_LEDGER_INCOMPLETE')
    deltas = {row.pk:targets[row.pk]-previous_targets[row.pk] for row in credits}
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
        by_wallet[credit.wallet_id]['income'] += deltas[credit.pk]
        by_wallet[credit.wallet_id]['ids'].append(credit.pk)
    hold_freezes,used_freezes = [],set()
    for hold in holds:
        if not 0 <= hold.recovered_amount <= hold.amount:
            raise RefundError('该单收益冻结记录异常',code='REFUND_LEDGER_INCOMPLETE')
        if hold.released_at or hold.cancelled_at:
            continue
        remaining = hold.amount-hold.recovered_amount
        freezes = [row for row in originals if row.tx_type == 'EARNINGS_FREEZE' and row.wallet_id == hold.wallet_id
                   and row.amount == -hold.amount and row.pk > hold.income_transaction_id and row.pk not in used_freezes]
        if not freezes or wallets[hold.wallet_id].frozen_amount < remaining:
            raise RefundError('该单冻结原流水或余额异常',code='REFUND_LEDGER_INCOMPLETE')
        freeze = freezes[0]
        used_freezes.add(freeze.pk)
        if -reversed_totals[freeze.pk] != hold.recovered_amount:
            raise RefundError('冻结已追回金额与冲正不一致',code='REFUND_LEDGER_INCOMPLETE')
        taken = min(deltas[hold.income_transaction_id],remaining)
        if taken:
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
        row = {'wallet_id':wallet_id,'account_id':owner_account_id(wallet),
               'role':'PLATFORM' if wallet_id in platform_ids else ('INVITER' if wallet.user_id == order.inviter_id else 'PROVIDER'),
               'income_amount':item['income'],'held_amount':item['held'],'deposit_amount':item['deposit'],
               'available_required':required,'available_balance':available,'protected_deposit_amount':protected,
               'shortfall':max(0,required-available),'original_transaction_ids':item['ids'],'source_safe':item['safe'],
               'explanation':'' if item['safe'] else '原抵扣后押金来源不明确；仅从可用余额追回，不动其他押金'}
        recoveries.append(row)
        if row['shortfall']:
            shortfalls.append({'wallet_id':wallet_id,'account_id':row['account_id'],'required':required,'available':available,'shortfall':row['shortfall']})
    valid = bool(amount and persisted['payment_status'] in PAID_STATES and persisted['status'] in ('PENDING','GRABBED','IN_SERVICE','COMPLETED'))
    if not settled:
        from engagement.confirmation import aftersale_deadline
        cutoff = aftersale_deadline(order)
        valid &= not order.consumption_confirmed_at and not (cutoff and timezone.now() >= cutoff and not order.aftersales.filter(status='OPEN').exists())
    facts = {'order':[order.pk,order.amount,order.customer_id,order.customer_account_id,order.provider_id,order.provider_account_id,
                     order.inviter_id,order.inviter_account_id,order.settlement_mode,order.business_cycle,str(order.consumption_confirmed_at),persisted],
             'amount':amount,'refunds':[(r.pk,r.amount,r.client_request_id) for r in prior_rows],
             'transactions':[(r.pk,r.wallet_id,r.tx_type,r.amount,r.reversal_of_id) for r in originals+reversals],
             'wallets':[(pk,r.balance,r.frozen_amount) for pk,r in sorted(wallets.items())],
             'profiles':[(pk,r.deposit_paid) for pk,r in sorted(profiles.items())],
             'holds':[(r.pk,r.amount,r.recovered_amount,str(r.released_at),str(r.cancelled_at),str(r.release_at)) for r in holds],
             'recoveries':recoveries}
    fingerprint = hashlib.sha256(json.dumps(facts,sort_keys=True,default=str).encode()).hexdigest()
    public = {'order_id':order.pk,'is_settled':settled,'eligible':bool(valid and not shortfalls),
              'original_amount':order.amount,'refunded_amount':prior,'remaining_amount':order.amount-prior,
              'refund_amount':amount,'remaining_after_refund':order.amount-prior-amount,'full_refund':prior+amount == order.amount,
              'recovery_total':sum(deltas.values()),'recoveries':recoveries,'shortfalls':shortfalls,
              'plan_token':signing.dumps({'order_id':order.pk,'refund_amount':amount,'fingerprint':fingerprint},salt=PLAN_SALT,compress=True)}
    return public,{'wallets':wallets,'credits':credits,'deltas':deltas,'hold_freezes':hold_freezes,
                  'deposit_pairs':deposit_pairs,'fingerprint':fingerprint}

def recover_settlement(order,refund,*,plan_token,operator,operator_account):
    if not authorized_refund(operator_account):
        raise RefundError('仅有退款权限的后台账号可追回已结算资金')
    preview,state = build_refund_plan(order,refund_amount=refund.amount,lock=True,exclude_refund_id=refund.pk)
    if preview['shortfalls']:
        raise RefundError('追回余额不足，本次退款未执行',code='REFUND_RECOVERY_INSUFFICIENT',data={'shortfalls':preview['shortfalls'],'preview':preview})
    try:
        token = signing.loads(plan_token or '',salt=PLAN_SALT,max_age=600)
    except signing.BadSignature:
        token = None
    if token != {'order_id':order.pk,'refund_amount':refund.amount,'fingerprint':state['fingerprint']}:
        raise RefundError('退款试算已变化或过期，请重新试算确认',code='REFUND_PLAN_CHANGED',data={'preview':preview})
    refund.plan_fingerprint = state['fingerprint']
    def reverse(original,amount,source):
        wallet = state['wallets'][original.wallet_id]
        before = wallet.balance
        wallet.balance += amount
        if wallet.balance < 0:
            raise RefundError('追回余额不足，本次退款未执行',code='REFUND_RECOVERY_INSUFFICIENT')
        wallet.save(update_fields=['balance','frozen_amount'])
        row = Transaction.objects.create(wallet=wallet,order=order,tx_type=original.tx_type,amount=amount,
            reversal_of=original,balance_before=before,balance_after=wallet.balance,
            operator=operator,operator_account=operator_account,remark=f'订单退款冲回：{order.order_no}'[:255])
        RefundRecoveryLine.objects.create(refund=refund,original_transaction=original,reversal_transaction=row,wallet=wallet,source=source,amount=amount)
    for debit,credit,profile,taken in state['deposit_pairs']:
        reverse(debit,taken,'DEPOSIT')
        reverse(credit,-taken,'DEPOSIT')
        profile.deposit_paid -= taken
        profile.save(update_fields=['deposit_paid'])
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
    refund.recovered_amount = preview['recovery_total']
    return list(state['wallets'].values())

@transaction.atomic
def apply_admin_refund(order_id,*,reason,operator,operator_account,refund_amount=None,client_request_id=None,plan_token=None,notify=True):
    from .services import refund_to_customer,refresh_escort_status
    from .state_machine import transition,log_only
    from .notifier import notify_order_update
    order = Order.objects.select_for_update().get(pk=order_id)
    if not authorized_refund(operator_account):
        raise RefundError('仅有退款权限的后台账号可操作退款')
    if replay_refund(order,client_request_id,refund_amount,reason):
        order._refund_replayed = True
        return order
    if order.payment_status not in PAID_STATES or order.status == 'CANCELLED':
        raise RefundError('订单当前没有可退余额')
    amount = parse_refund_amount(refund_amount,order.amount-refunded_amount(order))
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
    if notify:
        transaction.on_commit(lambda:notify_order_update(order))
    return order


def refund_summary(order,*,detailed=False,account=None):
    rows = list(OrderRefund.objects.filter(order=order).order_by('-created_at','-pk'))
    if not rows:
        return None
    provider = bool(account and account.account_type == 'PROVIDER')
    records,all_lines = [],[]
    for row in rows:
        item = {'id':row.pk,'amount':row.amount,'created_at':row.created_at,'reason':row.reason,'client_request_id':row.client_request_id}
        lines = list(row.recovery_lines.select_related('wallet','reversal_transaction')) if detailed or provider else []
        if detailed:
            item.update(recovered_amount=row.recovered_amount,benefit_effects=row.benefit_effects,
                recovery_lines=[{'original_transaction_id':r.original_transaction_id,'reversal_transaction_id':r.reversal_transaction_id,
                'wallet_id':r.wallet_id,'account_id':owner_account_id(r.wallet),'tx_type':r.reversal_transaction.tx_type,'amount':r.amount,'source':r.source} for r in lines])
            all_lines.extend(item['recovery_lines'])
        elif provider:
            item['recovered_amount'] = sum(-r.amount for r in lines if r.reversal_transaction.tx_type in ('INCOME','GIFT') and owner_account_id(r.wallet) == account.pk)
        records.append(item)
    total = sum(r.amount for r in rows)
    data = {'id':rows[0].pk,'amount':total,'original_amount':order.amount,'remaining_amount':max(0,order.amount-total),
            'refund_count':len(rows),'created_at':rows[0].created_at,'reason':rows[0].reason,'records':records}
    if detailed:
        data.update(recovered_amount=sum(r.recovered_amount for r in rows),recovery_lines=all_lines,benefit_effects=rows[0].benefit_effects)
    elif provider:
        owned = [r.pk for r in Wallet.objects.filter(pk__in=order.transactions.values('wallet_id')) if owner_account_id(r) == account.pk]
        data['recovered_amount'] = sum(r['recovered_amount'] for r in records)
        data['net_income'] = order.transactions.filter(wallet_id__in=owned,status='SUCCESS',tx_type__in=['INCOME','GIFT']).aggregate(v=Sum('amount'))['v'] or 0
        data['remaining_frozen_amount'] = sum(r.amount-r.recovered_amount for r in IncomeHold.objects.filter(
            wallet_id__in=owned,income_transaction__order=order,released_at__isnull=True,cancelled_at__isnull=True))
    return data
