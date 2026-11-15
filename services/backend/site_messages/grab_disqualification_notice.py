"""Recover notifications for actual parked-pool disqualifications."""
import logging

from django.conf import settings
from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def _recovery_enabled():
    return (getattr(settings, 'GRAB_RESULT_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def _record(order, source, people, kind):
    from .grab_cycle import schedule
    title = '本次抢单未成功'
    preview = f'「{order.service_name_snapshot}」接单资格已变化'
    if not _recovery_enabled():
        for account_id, cycle in people:
            schedule(account_id=account_id, cycle=cycle, title=title, preview=preview, order_id=order.pk)
        return
    source.disqualification_notice_intents = [
        {'account_id': account_id, 'business_cycle': cycle, 'title': title, 'preview': preview}
        for account_id, cycle in people]
    source.save(update_fields=['disqualification_notice_intents'])
    transaction.on_commit(lambda: deliver(kind, source.pk), robust=True)


def record_single(order, admission):
    from .grab_cycle import receipt_cycle
    cycle = receipt_cycle(admission.receipt, admission.account_id, round_id=admission.round_id, kind='SINGLE')
    _record(order, admission, [(admission.account_id, cycle)], 'single')


def record_team(order, team):
    from .grab_cycle import team_cycle
    _record(order, team, [(account_id, team_cycle(team, account_id))
                          for account_id in (team.captain_id, team.partner_id)], 'team')


def _source(kind):
    from orders.models import OrderGrabSingleAdmission, OrderGrabTeamDraft
    return {'single': OrderGrabSingleAdmission, 'team': OrderGrabTeamDraft}[kind]


def _deliver_one(kind, source_id, position, intent):
    from club_accounts.models import ClubAccount
    from .grab_cycle import receipt_cycle, team_cycle
    from .models import GrabDisqualificationNoticeBatch, GrabDisqualificationNoticeDelivery, Message
    from .notifier import notify_messages_after_commit

    with transaction.atomic():
        account_id = intent.get('account_id') if isinstance(intent, dict) else None
        account = (ClubAccount.objects.select_for_update().filter(pk=account_id).first()
                   if type(account_id) is int and account_id > 0 else None)
        source = _source(kind).objects.select_for_update().select_related('round__pool__order').filter(
            pk=source_id, status='DISQUALIFIED').first()
        if (not source or not isinstance(source.disqualification_notice_intents, list)
                or position >= len(source.disqualification_notice_intents)
                or source.disqualification_notice_intents[position] != intent):
            return
        batch, _ = GrabDisqualificationNoticeBatch.objects.get_or_create(**{kind if kind == 'team' else 'admission': source})
        delivery, created = GrabDisqualificationNoticeDelivery.objects.get_or_create(batch=batch, position=position)
        if not created:
            return
        cycle = intent.get('business_cycle') if isinstance(intent, dict) else None
        if kind == 'single':
            source_ok = (source.phase == 'PARKED' and source.account_id == account_id
                         and receipt_cycle(source.receipt, account_id, round_id=source.round_id, kind='SINGLE') == cycle)
        else:
            source_ok = (account_id in (source.captain_id, source.partner_id)
                         and team_cycle(source, account_id) == cycle)
        if (not source_ok or type(cycle) is not int or cycle <= 0 or not account
                or account.account_type != 'PROVIDER' or not account.license_membership_active
                or account.business_cycle != cycle):
            delivery.skipped_reason = '原抢单来源不可核验或收件账号周期已变化'
            delivery.save(update_fields=['skipped_reason'])
            return
        delivery.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
            type='ORDER', title=str(intent.get('title', ''))[:100], preview=str(intent.get('preview', ''))[:255],
            related_order_id=source.round.pool.order_id)
        delivery.save(update_fields=['message'])
        notify_messages_after_commit(account.pk, business_cycle=cycle)


def deliver(kind, source_id):
    from .models import GrabDisqualificationNoticeBatch, GrabDisqualificationNoticeDelivery
    source = _source(kind).objects.filter(pk=source_id, status='DISQUALIFIED').first()
    intents = source.disqualification_notice_intents if source else None
    if not isinstance(intents, list) or not intents:
        return
    for position, intent in enumerate(intents):
        try:
            _deliver_one(kind, source_id, position, intent)
        except Exception:
            log.exception('抢单失格通知待恢复 kind=%s source=%s position=%s', kind, source_id, position)
    try:
        with transaction.atomic():
            source = _source(kind).objects.select_for_update().filter(pk=source_id, status='DISQUALIFIED').first()
            if not source or source.disqualification_notice_intents != intents:
                return
            batch = GrabDisqualificationNoticeBatch.objects.filter(**{kind if kind == 'team' else 'admission': source}).first()
            if batch and set(GrabDisqualificationNoticeDelivery.objects.filter(batch=batch).values_list('position', flat=True)) == set(range(len(intents))):
                batch.completed_at = timezone.now()
                batch.save(update_fields=['completed_at'])
    except Exception:
        log.exception('抢单失格通知批次待恢复 kind=%s source=%s', kind, source_id)
