"""Durable store-local operator messages; creation is not external delivery."""
import hashlib
import json
import logging
from datetime import timedelta

from django.db import DatabaseError
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import APIException

from club_accounts.models import ClubAccount
from stores.db import transaction
from .models import ManualMessageCampaign, ManualMessageRecipient, Message

log = logging.getLogger(__name__)


class ManualMessageConflict(APIException):
    status_code = 409
    default_code = 'MANUAL_MESSAGE_REQUEST_CONFLICT'


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def existing_campaign(creator, request_id, digest):
    campaign = ManualMessageCampaign.objects.filter(creator=creator, request_id=request_id).first()
    if campaign and campaign.request_fingerprint != digest:
        raise ManualMessageConflict('请求编号已用于其他消息，请使用新的请求编号')
    return campaign


def stage_campaign(*, creator, request_id, digest, title, preview, detail, message_type, selector, account_cycles):
    with transaction.atomic():
        campaign, created = ManualMessageCampaign.objects.get_or_create(
            creator=creator, request_id=request_id,
            defaults={'request_fingerprint': digest, 'selector': selector, 'title': title, 'preview': preview,
                      'detail': detail, 'message_type': message_type})
        if not created:
            if campaign.request_fingerprint != digest:
                raise ManualMessageConflict('请求编号已用于其他消息，请使用新的请求编号')
            return campaign, False
        ManualMessageRecipient.objects.bulk_create([
            ManualMessageRecipient(campaign=campaign, account_id=account_id, business_cycle=identity[0],
                                   account_type=identity[1])
            for account_id, identity in account_cycles.items()
        ])
        return campaign, True


def deliver_one(recipient_id):
    """Commit message and its recipient receipt together; each recipient retries alone."""
    try:
        with transaction.atomic():
            target = (ManualMessageRecipient.objects.select_for_update()
                      .select_related('campaign').filter(pk=recipient_id).first())
            if not target or target.status != ManualMessageRecipient.Status.PENDING:
                return
            account = ClubAccount.objects.select_for_update().filter(pk=target.account_id, is_active=True,
                license_membership_active=True, business_cycle=target.business_cycle,
                account_type=target.account_type).first()
            if account and account.account_type == ClubAccount.AccountType.STAFF:
                from console.models import AdminMembership
                if not account.can_login or not AdminMembership.objects.select_for_update().filter(
                        account=account, is_active=True).exists():
                    account = None
            if not account:
                target.status = ManualMessageRecipient.Status.SKIPPED
                target.last_error = '账号停用或门店身份已变化'
                target.completed_at = timezone.now()
                target.save(update_fields=['status', 'last_error', 'completed_at'])
                return
            campaign = target.campaign
            message = Message.objects.create(recipient_account=account, recipient_business_cycle=target.business_cycle,
                type=campaign.message_type,
                title=campaign.title, preview=campaign.preview, detail=campaign.detail)
            target.message = message
            target.status, target.completed_at, target.last_error = target.Status.CREATED, timezone.now(), ''
            target.attempts += 1
            target.save(update_fields=['message', 'status', 'completed_at', 'last_error', 'attempts'])
            from .notifier import notify_messages_after_commit
            notify_messages_after_commit(account.pk, business_cycle=target.business_cycle)
    except Exception as exc:
        log.exception('站内群发记录创建失败: recipient_id=%s', recipient_id)
        try:
            with transaction.atomic():
                target = ManualMessageRecipient.objects.select_for_update().filter(pk=recipient_id,
                    status=ManualMessageRecipient.Status.PENDING).first()
                if target:
                    target.attempts += 1
                    target.last_error = type(exc).__name__[:100]
                    target.next_attempt_at = timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(target.attempts, 7)))
                    target.save(update_fields=['attempts', 'last_error', 'next_attempt_at'])
        except DatabaseError:
            log.exception('站内群发失败状态暂不可写: recipient_id=%s', recipient_id)


def process_campaign(campaign, *, target_ids=None, limit=200):
    rows = campaign.recipients.filter(status=ManualMessageRecipient.Status.PENDING)
    if target_ids is not None:
        rows = rows.filter(pk__in=target_ids)
    else:
        rows = rows.filter(next_attempt_at__lte=timezone.now())
    for target_id in rows.order_by('pk').values_list('pk', flat=True)[:limit]:
        deliver_one(target_id)


def campaign_result(campaign):
    counts = {row['status']: row['total'] for row in campaign.recipients.values('status').annotate(total=Count('pk'))}
    failed = campaign.recipients.filter(status=ManualMessageRecipient.Status.PENDING).exclude(last_error='').count()
    return {'campaign_id': campaign.pk, 'client_request_id': campaign.request_id,
            'creator_id': campaign.creator_id, 'created_at': campaign.created_at,
            'title': campaign.title, 'message_type': campaign.message_type, 'selector': campaign.selector,
            'total': sum(counts.values()),
            'count': counts.get(ManualMessageRecipient.Status.CREATED, 0),
            'pending_count': counts.get(ManualMessageRecipient.Status.PENDING, 0),
            'failed_count': failed, 'skipped_count': counts.get(ManualMessageRecipient.Status.SKIPPED, 0)}
