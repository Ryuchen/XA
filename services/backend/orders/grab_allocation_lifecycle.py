"""Optimistic candidate snapshots for the experimental grab allocator.

Writers already serialize changes through the order row. Read without locks
before the transaction, acquire all provider lifecycle locks in ID order,
then re-read the candidate set under the order lock before using it.
"""
from dataclasses import dataclass

from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import lock_provider_operations
from .models import OrderGrabSingleAdmission, OrderGrabSingleRoundState, OrderGrabTeamDraft, OrderGrabTeamRoundState


@dataclass(frozen=True)
class CandidateSnapshot:
    state: tuple | None
    rows: tuple
    accounts: tuple
    complete: bool


def _state(order_id, team):
    model = OrderGrabTeamRoundState if team else OrderGrabSingleRoundState
    return (model.objects.filter(round__pool__order_id=order_id).order_by('-round__number')
            .values_list('pk', 'round_id', 'status').first())


def _rows(state, team, *, lock=False):
    if not state or state[2] not in ('OPEN', 'PARKED'):
        return ()
    if team:
        from django.db.models import Q
        statuses = Q(status='UNVERIFIED')
        if state[2] == 'OPEN':
            statuses |= Q(status='WITHDRAWN', withdrawal_receipt__round_phase='PARKED')
        query = OrderGrabTeamDraft.objects.filter(statuses, round_id=state[1]).order_by('pk')
        fields = ('pk', 'captain_id', 'partner_id')
    else:
        query = OrderGrabSingleAdmission.objects.filter(round_id=state[1], phase=state[2], status='ACTIVE').order_by('pk')
        fields = ('pk', 'account_id', 'receipt_id')
    if lock:
        query = query.select_for_update()
    return tuple(query.values_list(*fields))


def capture(order_id, *, team):
    state = _state(order_id, team)
    rows = _rows(state, team)
    ids = {account_id for row in rows for account_id in (row[1:3] if team else (row[1],))}
    accounts = tuple(ClubAccount.objects.filter(pk__in=ids, account_type='PROVIDER').order_by('pk'))
    return CandidateSnapshot(state, rows, accounts, {a.pk for a in accounts} == ids)


def lock(snapshot):
    lock_provider_operations(snapshot.accounts)


def matches(snapshot, state, *, team):
    current = (state.pk, state.round_id, state.status) if state else None
    return snapshot.complete and snapshot.state == current and snapshot.rows == _rows(current, team, lock=True)


def accounts_unchanged(snapshot):
    """Detect a change while waiting without treating stable ineligibility as uncertainty."""
    return all(ClubAccount.objects.filter(pk=a.pk, account_type='PROVIDER', business_cycle=a.business_cycle,
        is_active=a.is_active, can_login=a.can_login, license_membership_active=a.license_membership_active).exists()
        for a in snapshot.accounts)
