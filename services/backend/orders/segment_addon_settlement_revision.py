"""Update unpaid segment earnings after actual, separately agreed add-on refunds."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderAddonRefund, OrderSegmentAddonSettlementRevision
from .settlement import SettlementError


def verify_revisions(manifest):
    from .segment_settlement import _hash
    from .models import OrderSegmentSettlementRevision, OrderPointsTransferFollowupFundingRevision
    from .points_voluntary_paid_evidence import verify_payment_revisions
    verify_payment_revisions(manifest)
    from .points_followup_paid_handover import verify_revisions as verify_handover_revisions
    verify_handover_revisions(manifest)

    def bridge(plan, target, *, after, before=None):
        """Connect add-on versions separated by the actual second main refund."""
        query = OrderSegmentSettlementRevision.objects.select_for_update().filter(
            manifest=manifest, created_at__gt=after).order_by('created_at', 'pk')
        if before is not None:
            query = query.filter(created_at__lt=before)
        payments = manifest.voluntary_payment_revisions.filter(created_at__gt=after)
        if before is not None:
            payments = payments.filter(created_at__lt=before)
        events = [(item.created_at, 'main', item) for item in query]
        events += [(item.created_at, 'payment', item) for item in payments]
        handovers = manifest.paid_handover_revisions.filter(created_at__gt=after)
        if before is not None:
            handovers = handovers.filter(created_at__lt=before)
        events += [(item.created_at, 'handover', item) for item in handovers]
        completions = manifest.paid_completion_revisions.filter(created_at__gt=after)
        if before is not None:
            completions = completions.filter(created_at__lt=before)
        events += [(item.created_at, 'completion', item) for item in completions]
        from .models import OrderTransferNegotiationEvidence
        ordinary = OrderTransferNegotiationEvidence.objects.filter(order_id=manifest.order_id,
            execution__termination_processed_at__gt=after).select_related('execution__proposal', 'execution__refund')
        if before is not None:
            ordinary = ordinary.filter(execution__termination_processed_at__lt=before)
        events += [(item.execution.termination_processed_at, 'ordinary_main', item) for item in ordinary
                   if item.execution.refund_id and item.execution.proposal.ordinary_paid_settlement_snapshot]
        if len({event[0] for event in events}) != len(events):
            raise SettlementError('结算版本时间无法区分')
        current = plan
        for _, kind, item in sorted(events, key=lambda event: event[0]):
            if kind == 'ordinary_main':
                from .negotiation_execution import verify_execution
                from .negotiation_termination import verify_termination_evidence
                from .negotiation_transfer_evidence import verify_locked
                paid = item.execution.proposal.ordinary_paid_settlement_snapshot
                if (before is not None or not isinstance(paid, dict) or paid.get('manifest_id') != manifest.pk
                        or paid.get('plan') != current or paid.get('plan_hash') != _hash(current)
                        or item.snapshot.get('ordinary_paid_settlement_snapshot') != paid
                        or not isinstance(target.get('ordinary_transfer_funding'), dict)):
                    raise SettlementError('普通首轮协商与已付清单版本不连续')
                try:
                    verify_execution(item.execution)
                    verify_termination_evidence(item.execution)
                    verify_locked(item.execution)
                    from .ordinary_first_paid_settlement import verify_saved_snapshot
                    verify_saved_snapshot(item.order, paid, item.execution.proposal.affected_accounts)
                    prior_people = {row['account_id']: row for row in current.get('people', [])}
                    next_people = {row['account_id']: row for row in target.get('people', [])}
                    if any(prior_people.get(row['account_id']) != next_people.get(row['account_id'])
                           for row in paid['people']):
                        raise ValidationError('普通首轮协商改变了已付人员收益')
                    old_parts = current.get('points_funding', {}).get('addons', [])
                    new_parts = target['ordinary_transfer_funding'].get('addons', [])
                    paid_accounts = {row['account_id'] for row in paid['people']}
                    if [part for part in old_parts if part['provider_account_id'] in paid_accounts] != [
                            part for part in new_parts if part['provider_account_id'] in paid_accounts]:
                        raise ValidationError('普通首轮协商改变了已付加购收益')
                except (ValidationError, KeyError, TypeError) as exc:
                    raise SettlementError('普通首轮协商的已付凭证异常') from exc
                current = target
                continue
            if kind in ('payment', 'handover', 'completion'):
                if item.before_plan != current or item.before_plan_hash != _hash(current):
                    raise SettlementError('加购付款与结算版本不连续')
                current = item.after_plan
                continue
            from .models import PointsRefundRoundEvidence
            round_version = PointsRefundRoundEvidence.objects.filter(posting=item.posting, previous__isnull=False).first()
            if round_version:
                from .points_round_paid_settlement import verify_revision_locked
                verify_revision_locked(manifest, item, before_plan=current)
                current = item.after_plan
                continue
            source = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=item.posting).first()
            prior = source.prior_manifest if source else None
            if (not source or not isinstance(prior, dict) or prior.get('manifest_id') != manifest.pk
                    or prior.get('plan') != current or prior.get('plan_hash') != _hash(current)
                    or item.before_plan_hash != _hash(current) or item.after_plan_hash != _hash(item.after_plan)
                    or item.posting.proposal.case.order_id != manifest.order_id
                    or item.created_at < item.posting.points_refund.refund.created_at
                    or set(item.posting.proposal.affected_accounts).intersection(
                        row['account_id'] for row in prior.get('people', []))):
                raise SettlementError('主单退款与加购结算版本无法衔接')
            from .points_transfer_followup_revision import _digest
            if source.evidence_hash != _digest(source.posting, source.first_posting_id, prior,
                    source.allocations, source.releases, source.funding, source.revision, source.prior_required_units,
                    source.pending_handover_snapshot):
                raise SettlementError('主单退款资金版本摘要异常')
            from .points_transfer_paid_addons import unchanged
            unchanged(prior, item.after_plan.get('points_funding', {}).get('addons', []))
            current = item.after_plan
        if current != target:
            raise SettlementError('主单退款与加购结算版本不连续')

    revisions = list(manifest.addon_revisions.select_for_update().order_by('version'))
    previous = None
    previous_at = None
    for index, revision in enumerate(revisions, start=1):
        before, after = revision.before_plan, revision.after_plan
        if (revision.version != index or revision.before_plan_hash != _hash(before)
                or revision.after_plan_hash != _hash(after)):
            raise SettlementError('加购退款结算版本记录不一致')
        if previous is not None and before != previous:
            bridge(previous, before, after=previous_at, before=revision.created_at)
        before_ids = before.get('points_funding', {}).get('addon_refund_ids', [])
        after_ids = after.get('points_funding', {}).get('addon_refund_ids', [])
        if (not isinstance(before_ids, list) or not isinstance(after_ids, list)
                or len(set(after_ids)) != len(after_ids) or not set(before_ids).issubset(after_ids)
                or sorted(set(after_ids) - set(before_ids)) != revision.refund_ids
                or OrderAddonRefund.objects.filter(order_id=manifest.order_id,
                    pk__in=revision.refund_ids).count() != len(revision.refund_ids)):
            raise SettlementError('加购退款结算版本缺少退款凭证')
        from .models import PointsVoluntaryPaidRefundEvidence
        evidence = list(PointsVoluntaryPaidRefundEvidence.objects.filter(
            manifest=manifest, refund_id__in=revision.refund_ids).select_related('funding_run__refund__order'))
        if evidence:
            from .points_voluntary_paid_evidence import verify_current_read
            from .points_transfer_paid_addons import unchanged
            if len(evidence) != len(revision.refund_ids):
                raise SettlementError('已付退款结算版本凭证不完整')
            try:
                old_people = {item['account_id']: item for item in before.get('people', [])}
                new_people = {item['account_id']: item for item in after.get('people', [])}
                for item in evidence:
                    paid = verify_current_read(item.funding_run)
                    if not paid or paid['plan'] != before:
                        raise ValidationError('已付退款的原清单不一致')
                    unchanged(paid, after.get('points_funding', {}).get('addons', []))
                    if any(old_people.get(person['account_id']) != new_people.get(person['account_id'])
                           for person in paid['people']):
                        raise ValidationError('已付人员的主单收益发生变化')
            except (ValidationError, KeyError, TypeError) as exc:
                raise SettlementError('已付退款结算版本流水异常') from exc
        from .models import OrdinaryPaidAddonRefundEvidence
        ordinary_evidence = list(OrdinaryPaidAddonRefundEvidence.objects.filter(
            manifest=manifest, refund_id__in=revision.refund_ids).select_related('refund__proposal', 'refund__order'))
        if ordinary_evidence:
            from .ordinary_paid_addon_refund import verify_locked
            if len(ordinary_evidence) != len(revision.refund_ids):
                raise SettlementError('普通已付退款结算版本凭证不完整')
            try:
                for item in ordinary_evidence:
                    paid = verify_locked(item)
                    if paid.get('plan') != before or paid.get('plan_hash') != revision.before_plan_hash:
                        raise ValidationError('已付退款的原清单不一致')
            except (ValidationError, KeyError, TypeError) as exc:
                raise SettlementError('普通已付退款结算版本流水异常') from exc
        previous = after
        previous_at = revision.created_at
    if previous is not None and (manifest.plan != previous or manifest.plan_hash != _hash(previous)):
        bridge(previous, manifest.plan, after=previous_at)
        if manifest.plan_hash != _hash(manifest.plan):
            raise SettlementError('加购退款结算版本与当前方案不一致')


def transition_locked(order, manifest, plan, terms):
    from .points_checkout_test import require_isolated_checkout
    from .segment_settlement import _hash, _validate_records

    from .points_service_payment import needs_integrated_processing
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    formal_ordinary = bool(not needs_integrated_processing(order) and not order.refund_records.exists()
        and ordinary_transfer_permits(order)
        and manifest.ordinary_paid_addon_refunds.exists())
    if not formal_ordinary:
        try:
            require_isolated_checkout()
        except ValidationError as exc:
            raise SettlementError('加购退款后的逐段结算尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block
            or (not formal_ordinary and not getattr(settings, 'ADDON_SEGMENT_REFUND_REVISION_INTERNAL_TESTING', False))
            or manifest.order_id != order.pk or manifest.finalized_at or order.settled_at):
        raise SettlementError('加购退款后的逐段结算尚未开放')
    verify_revisions(manifest)
    before = manifest.plan
    if not isinstance(before, dict) or manifest.plan_hash != _hash(before):
        raise SettlementError('退款前逐段结算方案待核对')
    stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id',
              'inviter_account_id', 'inviter_id', 'inviter_rate', 'deposit_percent',
              'aftersale_hours', 'freeze_hours', 'completed_at')
    if any(before.get(key) != terms.get(key) for key in stable):
        raise SettlementError('加购退款改变了主单履约份额')
    before_people = {row['account_id']: row for row in before.get('people', [])}
    after_people = {row['account_id']: row for row in terms.get('people', [])}
    if (set(before_people) != set(after_people)
            or any(before_people[account]['segment_ids'] != row['segment_ids']
                   or row['income'] > before_people[account]['income']
                   for account, row in after_people.items())):
        raise SettlementError('加购退款改变了主单履约人员或增加了收益')
    before_ids = before.get('points_funding', {}).get('addon_refund_ids', [])
    after_ids = terms.get('points_funding', {}).get('addon_refund_ids', [])
    if (not isinstance(before_ids, list) or not isinstance(after_ids, list)
            or len(set(after_ids)) != len(after_ids) or not set(before_ids).issubset(after_ids)):
        raise SettlementError('加购退款凭证列表不一致')
    added = sorted(set(after_ids) - set(before_ids))
    if not added or OrderAddonRefund.objects.filter(order=order, pk__in=added).count() != len(added):
        raise SettlementError('缺少实际发生的加购退款')
    if formal_ordinary:
        from .models import OrdinaryPaidAddonRefundEvidence
        from .ordinary_paid_addon_refund import verify_locked
        evidence = list(OrdinaryPaidAddonRefundEvidence.objects.filter(manifest=manifest,
            refund_id__in=added).select_related('refund__proposal', 'refund__order'))
        if len(evidence) != len(added):
            raise SettlementError('已付加购退款来源凭证缺失')
        try:
            for item in evidence:
                paid = verify_locked(item)
                if paid.get('plan') != before or paid.get('plan_hash') != manifest.plan_hash:
                    raise ValidationError('已付退款前清单已变化')
        except ValidationError as exc:
            raise SettlementError('已付加购退款来源待核对') from exc
    _validate_records(order, manifest, plan)
    version = manifest.addon_revisions.count() + 1
    with transaction.atomic():
        OrderSegmentAddonSettlementRevision.objects.create(manifest=manifest, version=version,
            refund_ids=added, before_plan=before, before_plan_hash=manifest.plan_hash,
            after_plan=terms, after_plan_hash=_hash(terms))
        manifest.plan, manifest.plan_hash = terms, _hash(terms)
        manifest.save(update_fields=['plan', 'plan_hash'])
        return _validate_records(order, manifest, plan)
