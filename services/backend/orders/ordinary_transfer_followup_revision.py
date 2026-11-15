"""Preserve an existing segment manifest across a later agreement."""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest, OrdinaryTransferFollowupSettlementRevision


def _digest(round_id, manifest_id, plan, plan_hash, settled):
    data = [round_id, manifest_id, plan, plan_hash, settled]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def record_locked(round_record):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('逐轮结算修订须在订单事务内保存')
    existing = OrdinaryTransferFollowupSettlementRevision.objects.select_for_update().filter(round=round_record).first()
    if existing:
        return verify_locked(existing)
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=round_record.order).first()
    if manifest and manifest.finalized_at:
        raise ValidationError('订单已最终结算，不能追加售后')
    settled = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(
        manifest__order=round_record.order).order_by('pk').values('id', 'account_id', 'amount', 'segment_ids'))
    if settled != round_record.facts.get('settled_facts'):
        raise ValidationError('已结算履约段已变化，请重新确认方案')
    plan, plan_hash = (manifest.plan, manifest.plan_hash) if manifest else (None, '')
    if manifest:
        from .segment_settlement import _hash
        if plan_hash != _hash(plan):
            raise ValidationError('原逐段结算清单待核对')
    return OrdinaryTransferFollowupSettlementRevision.objects.create(round=round_record, manifest=manifest,
        previous_plan=plan, previous_plan_hash=plan_hash, settled_people=settled,
        facts_hash=_digest(round_record.pk, manifest.pk if manifest else None, plan, plan_hash, settled))


def verify_locked(revision):
    if (revision.facts_hash != _digest(revision.round_id, revision.manifest_id, revision.previous_plan,
                                       revision.previous_plan_hash, revision.settled_people)
            or revision.settled_people != revision.round.facts.get('settled_facts')):
        raise ValidationError('追加协商的原结算清单凭证异常')
    if revision.previous_plan is not None:
        from .segment_settlement import _hash
        if not revision.manifest_id or revision.previous_plan_hash != _hash(revision.previous_plan):
            raise ValidationError('追加协商的原结算快照异常')
    elif revision.manifest_id or revision.previous_plan_hash:
        raise ValidationError('追加协商结算版本来源异常')
    return revision


def transition_locked(order, manifest, plan, terms):
    """Update only an unpaid remainder while retaining every paid record."""
    from .models import OrdinaryTransferFollowupRound
    row = OrdinaryTransferFollowupRound.objects.select_for_update().filter(
        order=order, execution__termination_processed_at__isnull=False).order_by('-sequence', '-pk').first()
    if not row:
        raise ValidationError('追加协商轮次缺失')
    revision = verify_locked(row.settlement_revision)
    if (revision.manifest_id != manifest.pk or revision.previous_plan != manifest.plan
            or revision.previous_plan_hash != manifest.plan_hash):
        raise ValidationError('原逐段结算清单已变化，请先核对')
    people = {person.account_id: person for person in plan.persons}
    for paid in revision.settled_people:
        person = people.get(paid['account_id'])
        if not person or person.income != paid['amount'] or list(person.segment_ids) != paid['segment_ids']:
            raise ValidationError('追加协商不得改变已结算的履约段')
    from .segment_settlement import _hash, _validate_records
    manifest.plan, manifest.plan_hash = terms, _hash(terms)
    manifest.save(update_fields=['plan', 'plan_hash'])
    return _validate_records(order, manifest, plan)
