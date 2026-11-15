"""Record after-sale closure bounds without inventing a commit timestamp."""
from datetime import timezone
import logging
import os
import time

from django.db import DatabaseError
from stores.context import current_store
from stores.db import connection, transaction

logger = logging.getLogger(__name__)


def _sample():
    connection.ensure_connection()
    driver = connection.connection
    before = time.monotonic_ns()
    with connection.cursor() as cursor:
        cursor.execute('SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()')
        instant, identity, connection_id = cursor.fetchone()
    after = time.monotonic_ns()
    if (connection.connection is not driver or not identity or not connection_id or after < before):
        raise ValueError('收益资格时钟无法核对')
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant, driver, {'database_identity': identity, 'connection_id': connection_id,
        'process_id': os.getpid(), 'before_ns': before, 'after_ns': after}


def record_closure(case):
    """Called under order, money mutex and affected profile locks."""
    if connection.vendor != 'mysql':
        return None
    from .models import IncomeAfterSaleClosureEvidence, IncomeHold
    if not IncomeHold.objects.filter(income_transaction__order_id=case.order_id,
                                      released_at__isnull=True, cancelled_at__isnull=True).exists():
        return None
    existing = IncomeAfterSaleClosureEvidence.objects.filter(case=case, case_opened_at=case.created_at).first()
    if existing:
        return existing
    lower, driver, sample = _sample()
    record = IncomeAfterSaleClosureEvidence.objects.create(case=case, case_opened_at=case.created_at,
        lower_at=lower, clock_evidence={'lower': sample})
    store = current_store()
    alias, slug = connection.alias, store.slug if store else None
    transaction.on_commit(lambda: _observe(record.pk, alias, slug, driver), robust=True)
    return record


def record_hold(hold):
    """Call after creating a hold while holding the store money mutex."""
    if connection.vendor != 'mysql':
        return None
    from .models import IncomeAfterSaleClosureEvidence
    from .refund_priority import case_snapshot, episode_snapshot
    order = hold.income_transaction.order
    cases = []
    if order:
        for case in order.aftersales.order_by('pk'):
            if case.status != 'OPEN':
                cases.append(case_snapshot(case))
            if isinstance(case.history, list):
                cases.extend(episode_snapshot(case, entry) for entry in case.history
                    if isinstance(entry, dict) and entry.get('status') in ('RESOLVED', 'REFUNDED', 'PARTIALLY_REFUNDED'))
    lower, driver, sample = _sample()
    record = IncomeAfterSaleClosureEvidence.objects.create(hold=hold, lower_at=lower,
        clock_evidence={'lower': sample}, observed_cases=cases)
    store = current_store()
    transaction.on_commit(lambda: _observe(record.pk, connection.alias, store.slug if store else None, driver), robust=True)
    return record


def attach_closure(case):
    """Attach evidence to an already appended closure history entry."""
    from .refund_deposit import enabled
    if enabled():
        evidence = record_closure(case)
        if evidence:
            case.history[-1]['income_priority_closure_id'] = evidence.pk


def pending_repayment_hold(income):
    """Verify funds are still frozen; absence of repayment is not repayment."""
    from .models import IncomeAfterSaleClosureEvidence, IncomeHold, Transaction
    hold = IncomeHold.objects.filter(income_transaction=income, wallet_id=income.wallet_id,
        amount=income.amount, deposit_amount=0, recovered_amount=0,
        released_at__isnull=True, cancelled_at__isnull=True).first()
    if not hold or not IncomeAfterSaleClosureEvidence.objects.filter(hold=hold).exists():
        return False
    freezes = Transaction.objects.filter(wallet_id=income.wallet_id, order_id=income.order_id,
        tx_type=Transaction.TxType.EARNINGS_FREEZE, status=Transaction.Status.SUCCESS,
        amount=-hold.amount, reversal_of__isnull=True, created_at__gte=hold.created_at).order_by('pk')
    return any(tx.balance_after == tx.balance_before + tx.amount and not tx.reversals.exists()
               for tx in freezes[:2])


def initial_deposit_verified(hold):
    from django.conf import settings
    from .models import Transaction
    if not hold.deposit_amount:
        return not hold.initial_deposit_debit_id and not hold.initial_deposit_credit_id
    debit, credit = hold.initial_deposit_debit, hold.initial_deposit_credit
    return bool(debit and credit and debit.wallet_id == hold.wallet_id
        and credit.deposit_pair_id == debit.pk
        and debit.deposit_owner_id == credit.deposit_owner_id == hold.wallet.account_id
        and debit.deposit_business_cycle == credit.deposit_business_cycle == hold.account_business_cycle
        and hold.account_business_cycle is not None
        and credit.wallet.user_id and credit.wallet.user.username == settings.PLATFORM_SYSTEM_USERNAME
        and debit.order_id == credit.order_id == hold.income_transaction.order_id
        and debit.tx_type == Transaction.TxType.DEPOSIT and debit.amount == -hold.deposit_amount
        and credit.tx_type == Transaction.TxType.DEPOSIT_INCOME and credit.amount == hold.deposit_amount
        and debit.status == credit.status == Transaction.Status.SUCCESS
        and debit.balance_after == debit.balance_before + debit.amount
        and credit.balance_after == credit.balance_before + credit.amount
        and not debit.reversal_of_id and not credit.reversal_of_id
        and not debit.reversals.exists() and not credit.reversals.exists()
        and hold.income_transaction.amount == hold.amount + hold.deposit_amount)


def _observe(record_id, alias, slug, driver):
    from orders.grab_clock_contract import valid_bounds
    from .models import IncomeAfterSaleClosureEvidence
    store = current_store()
    if not store or store.slug != slug or connection.alias != alias:
        return
    try:
        upper, current_driver, upper_sample = _sample()
        if current_driver is not driver:
            return
        with transaction.atomic():
            record = IncomeAfterSaleClosureEvidence.objects.select_for_update().filter(pk=record_id).first()
            if not record or record.upper_at is not None:
                return
            lower_sample = record.clock_evidence.get('lower')
            proof = {'lower': lower_sample, 'upper': upper_sample}
            identity = upper_sample['database_identity']
            if not valid_bounds(record.lower_at, upper, proof, identity=identity):
                return
            record.upper_at, record.clock_evidence = upper, proof
            record.save(update_fields=['upper_at', 'clock_evidence'])
    except (DatabaseError, ValueError, TypeError):
        logger.exception('售后结案收益资格时间待核对 evidence=%s', record_id)


def recover_pending(hold_ids, order_ids):
    """Use a later money-mutex event as an upper bound, never as the commit time."""
    from django.db.models import Q
    from orders.grab_clock_contract import valid_bounds
    from orders.models import AfterSale
    from .models import IncomeAfterSaleClosureEvidence
    if connection.vendor != 'mysql' or not connection.in_atomic_block:
        return
    case_ids = list(AfterSale.objects.filter(order_id__in=order_ids).values_list('pk', flat=True))
    rows = IncomeAfterSaleClosureEvidence.objects.select_for_update().filter(
        Q(hold_id__in=hold_ids) | Q(case_id__in=case_ids), upper_at__isnull=True).order_by('pk')
    for record in rows:
        upper, _, sample = _sample()
        proof = {'lower': record.clock_evidence.get('lower'), 'upper': sample}
        if valid_bounds(record.lower_at, upper, proof, identity=sample['database_identity']):
            record.upper_at, record.clock_evidence = upper, proof
            record.save(update_fields=['upper_at', 'clock_evidence'])
            continue
        original = record.clock_evidence.get('lower')
        if (not isinstance(original, dict) or original.get('database_identity') != sample['database_identity']
                or upper < record.lower_at):
            continue
        # The original row was read after its transaction committed. The
        # caller holds the same store money mutex as the original writer, so
        # this later sample is a durable, auditable upper bound even after a
        # worker or connection change. It is deliberately not the commit time.
        witness = IncomeAfterSaleClosureEvidence.objects.create(barrier=True, lower_at=upper,
                                                                 clock_evidence={'lower': sample})
        record.upper_at = upper
        record.clock_evidence = {'lower': original, 'recovery_event_id': witness.pk}
        record.save(update_fields=['upper_at', 'clock_evidence'])


def valid_priority_bounds(record, *, identity):
    from orders.grab_clock_contract import valid_bounds
    from .models import IncomeAfterSaleClosureEvidence
    evidence = record.clock_evidence
    if valid_bounds(record.lower_at, record.upper_at, evidence, identity=identity):
        return True
    if not isinstance(evidence, dict) or set(evidence) != {'lower', 'recovery_event_id'}:
        return False
    lower = evidence['lower']
    witness = IncomeAfterSaleClosureEvidence.objects.filter(pk=evidence['recovery_event_id'],
        pk__gt=record.pk, barrier=True, lower_at=record.upper_at).first()
    sample = witness.clock_evidence.get('lower') if witness and isinstance(witness.clock_evidence, dict) else None
    keys = {'database_identity', 'connection_id', 'process_id', 'before_ns', 'after_ns'}
    return bool(isinstance(lower, dict) and set(lower) == keys and isinstance(sample, dict) and set(sample) == keys
        and all(type(value.get(key)) is int and value[key] >= 0 for value in (lower, sample)
                for key in ('connection_id', 'process_id', 'before_ns', 'after_ns'))
        and all(value['connection_id'] > 0 and value['process_id'] > 0 and value['before_ns'] <= value['after_ns']
                for value in (lower, sample))
        and lower.get('database_identity') == identity and sample.get('database_identity') == identity
        and record.upper_at >= record.lower_at)
