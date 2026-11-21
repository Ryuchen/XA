"""Agreed reassignment of a paid add-on among the main order's participants."""
import hashlib
import json
from collections.abc import Mapping
from functools import wraps

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from stores.db import transaction
from .addon_intents import _commission
from .aftersale_scope import applies_to
from .models import (Order, OrderAddonHandover, OrderAddonHandoverDecision, OrderAddonHandoverShare,
                     OrderAddonPayment, OrderParticipationSegment, OrderProvider)


def _ordinary_funding_scope(func):
    @wraps(func)
    def wrapped(self, request, order_id, payment_id, *args, **kwargs):
        from .points_funding_evidence import ordinary_handover_read_scope
        handover_id = kwargs.get('handover_id') or (args[0] if args else None)
        target_id = request.data.get('target_provider_account_id') if isinstance(request.data, Mapping) else None
        with ordinary_handover_read_scope(order_id, payment_id, target_id, handover_id):
            return func(self, request, order_id, payment_id, *args, **kwargs)
    return wrapped


def enabled():
    from .points_transfer_refund_access import permits
    from .points_business_access import permits as business_permits
    return bool(permits(phase='transfer') or business_permits(phase='handover', kind='addon')
                or getattr(settings, 'ADDON_HANDOVER_INTERNAL_TESTING', False))


def _replay_followup_create(request, order_id, payment_id):
    """Return an existing create request without changing money."""
    if 'order:dispatch' not in get_account_permissions(request.account):
        return Response({'code': 403, 'msg': '无权安排加购负责人'}, status=403)
    body = request.data
    request_id = body.get('request_id') if isinstance(body, Mapping) else None
    if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 64:
        return None
    # Lock the same tenant order before its payment and request record.  The
    # handler still performs all normal checks when this is not a replay.
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        return None
    from .financial_lock import lock_order_money
    from .models import OrderPointsFundingAllocation
    from django.db.models import Q
    from users.models import PassRefundRepaymentReceipt
    lock_order_money()
    receipt_ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order)).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids):
        raise ValidationError('原加购接替的到账凭证缺失')
    payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
    if not payment:
        return None
    previous = (OrderAddonHandover.objects.select_for_update().filter(payment=payment,
        request_id=request_id.strip()).select_related('source_segment', 'target_segment').first())
    if not previous:
        return None
    if previous.created_by_id != request.account.pk or previous.request_hash != _fingerprint(dict(body)):
        raise ValidationError('请求编号已用于其他换人方案')
    from .models import OrderAddonFundingReleaseRun
    if previous.status in ('PENDING', 'REJECTED'):
        from .points_transfer_followup_handover_scope import _verify_rejected
        _verify_rejected(payment, previous, pending=previous.status == 'PENDING')
        return Response({'code': 0, 'data': _data(previous, account=request.account)})
    if previous.status == 'SUPERSEDED':
        from .models import PointsHandoverRefundInvalidation
        from .points_followup_handover_invalidation import verify as verify_invalidation
        from .points_business_access import _bind
        record = PointsHandoverRefundInvalidation.objects.filter(handover=previous).select_related(
            'handover__payment__intent', 'refund__payment__intent').first()
        if not record:
            raise ValidationError('原加购接替的失效凭证缺失')
        witness = next((receipt for receipt in receipts if OrderPointsFundingAllocation.objects.filter(
            redemption__order=order, receipt_id=receipt.pk).exists()), None)
        with _bind(phase='refund', kind='transfer', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            verify_invalidation(record, receipt_id=witness.pk if witness else None)
        return Response({'code': 0, 'data': _data(previous, account=request.account)})
    if previous.status != 'ACCEPTED':
        raise ValidationError('原加购接替状态待核对')
    from .points_transfer_followup_addon_version import digest
    run = OrderAddonFundingReleaseRun.objects.filter(handover=previous).first()
    snapshot = run.points_followup_snapshot if run else None
    if (not previous.accepted_at or not run or not isinstance(snapshot, dict) or snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'
            or snapshot.get('addon_handover_id') != previous.pk or run.points_followup_hash != digest(run, snapshot)
            or run.created_at < previous.accepted_at or run.refund_id or run.payment_id
            or sum(run.releases.values_list('amount', flat=True)) != run.released_amount
            or run.releases.exclude(Q(allocation__redemption__order=order)
                                    | Q(allocation__service_order=order)).exists()):
        raise ValidationError('原加购接替资金凭证待核对')
    from .points_transfer_followup_handover_scope import verify_event
    from .addon_funding_release import verify_run
    witness = next((item for item in receipts if OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), receipt=item).exists()), None)
    from .points_transfer_refund_access import after_transfer
    pending = frozenset([payment.pk, *OrderAddonHandover.objects.filter(
        payment__intent__order=order, status='PENDING').values_list('payment_id', flat=True)])
    # The settlement scope additionally verifies the finalized manifest and
    # actual credits before returning an already completed request.
    with after_transfer(order.pk, phase='settlement' if order.settled_at else 'refund',
                        pending_payment_id=pending, allow_voluntary_history=True):
        verify_event(payment, previous, receipt_id=witness.pk if witness else None)
        verify_run(run)
    return Response({'code': 0, 'data': _data(previous, account=request.account)})


def _points_handover_scope(method):
    from functools import wraps
    @wraps(method)
    def wrapped(self, request, order_id, *args, **kwargs):
        from .points_service_payment import needs_integrated_processing
        from .models import OrderOriginalShareTransfer
        order = Order.objects.filter(pk=order_id).first()
        if (order and needs_integrated_processing(order)
                and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                and transaction.get_connection().vendor == 'mysql'):
            payment_id = args[0] if args else kwargs.get('payment_id')
            creation = len(args) < 2 and kwargs.get('handover_id') is None
            if (creation and payment_id and order.refund_records.count() == 2
                    and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                replay = _replay_followup_create(request, order_id, payment_id)
                if replay is not None:
                    return replay
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                if order.refund_records.exists():
                    payment_id = args[0] if args else kwargs.get('payment_id')
                    handover_id = args[1] if len(args) > 1 else kwargs.get('handover_id')
                    voluntary = (OrderAddonHandover.objects.filter(pk=handover_id, payment_id=payment_id,
                        payment__intent__order=order, kind='VOLUNTARY').exists() if handover_id else
                        isinstance(request.data, Mapping) and request.data.get('kind') == 'VOLUNTARY')
                    if order.refund_records.count() >= 3:
                        if creation and isinstance(request.data, Mapping):
                            request_id = request.data.get('request_id')
                            if isinstance(request_id, str):
                                handover_id = (OrderAddonHandover.objects.filter(payment_id=payment_id,
                                    payment__intent__order=order, request_id=request_id.strip(),
                                    kind='VOLUNTARY' if voluntary else 'STANDARD').values_list('pk', flat=True).first())
                        if voluntary:
                            from .points_transfer_refund_access import post_third_addon_handover
                            scope = post_third_addon_handover(order.pk, payment_id, handover_id=handover_id)
                        else:
                            from .points_transfer_refund_access import post_third_standard_handover
                            scope = post_third_standard_handover(order.pk, payment_id, handover_id=handover_id,
                                target_account_id=request.data.get('target_provider_account_id')
                                    if isinstance(request.data, Mapping) else None)
                    elif voluntary:
                        from .points_transfer_refund_access import voluntary_handover
                        scope = voluntary_handover(order.pk, payment_id, handover_id=handover_id,
                            target_account_id=request.data.get('target_provider_account_id')
                                if isinstance(request.data, Mapping) else None)
                    else:
                        from .points_transfer_refund_access import post_refund_addon_handover
                        scope = post_refund_addon_handover(order.pk, payment_id,
                            replay_handover_id=handover_id,
                            target_account_id=request.data.get('target_provider_account_id')
                                if isinstance(request.data, Mapping) else None)
                else:
                    from .points_transfer_refund_access import after_transfer, transfer_capture
                    payment_id = args[0] if args else kwargs.get('payment_id')
                    handover_id = args[1] if len(args) > 1 else kwargs.get('handover_id')
                    replay = handover_id and OrderAddonHandover.objects.filter(pk=handover_id, payment_id=payment_id,
                        payment__intent__order=order, kind='STANDARD', status='ACCEPTED',
                        decisions__account=request.account, decisions__accepted=True).exists()
                    if replay:
                        request._points_transferred_handover_replay = True
                        scope = after_transfer(order.pk, phase='report', pending_payment_id=payment_id, read_only=True)
                    else:
                        scope = transfer_capture(order.pk, addon_handover=True, pending_payment_id=payment_id)
            else:
                from .points_business_access import service_order
                payment_id = args[0] if args else kwargs.get('payment_id')
                handover_id = args[1] if len(args) > 1 else kwargs.get('handover_id')
                replay = (order.status == Order.Status.COMPLETED and payment_id and handover_id
                          and OrderAddonHandover.objects.filter(pk=handover_id, payment_id=payment_id,
                              payment__intent__order=order, status='ACCEPTED',
                              decisions__account=request.account).exists())
                request._points_team_handover_replay = replay
                scope = service_order(order.pk, phase='report' if replay else 'handover', addon=True)
            with scope:
                return method(self, request, order_id, *args, **kwargs)
        return method(self, request, order_id, *args, **kwargs)
    return wrapped


def _handover_lifecycle(method):
    """Lock both providers before the order and funding locks in confirmation."""
    from functools import wraps

    @wraps(method)
    def wrapped(self, request, order_id, payment_id, handover_id):
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return method(self, request, order_id, payment_id, handover_id)
        snapshot = (OrderAddonHandover.objects.filter(pk=handover_id, payment_id=payment_id,
            payment__intent__order_id=order_id, decisions__account=request.account)
            .values_list('source_segment__provider_account_id', 'target_segment__provider_account_id').first())
        if snapshot is None:
            return Response({'code': 404, 'msg': '换人方案不存在'}, status=404)
        from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operations
        from engagement.settlement_lifecycle import capture
        try:
            accounts = capture(order_id)
            if None in snapshot or not set(snapshot).issubset({account.pk for account in accounts}):
                raise ValueError('换人方案的陪玩账号待核对')
            with transaction.atomic():
                lock_provider_operations(accounts)
                request._addon_handover_lifecycle = (snapshot, {account.pk: account for account in accounts})
                return method(self, request, order_id, payment_id, handover_id)
        except (ProviderLifecycleError, ValueError) as exc:
            raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
    return wrapped


def _handover_creation_lifecycle(method):
    """Capture the current owner and serialize a new proposal with closure."""
    from functools import wraps

    @wraps(method)
    def wrapped(self, request, order_id, payment_id):
        target_id = request.data.get('target_provider_account_id') if isinstance(request.data, Mapping) else None
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or 'order:dispatch' not in get_account_permissions(request.account)
                or type(target_id) is not int or target_id <= 0):
            return method(self, request, order_id, payment_id)
        payment = (OrderAddonPayment.objects.select_related('intent')
                   .filter(pk=payment_id, intent__order_id=order_id).first())
        if not payment:
            return method(self, request, order_id, payment_id)
        request_id = request.data.get('request_id')
        if isinstance(request_id, str) and payment.handovers.filter(request_id=request_id.strip()).exists():
            # The locked handler validates actor and fingerprint before replaying.
            return method(self, request, order_id, payment_id)
        previous, shares = current_responsibilities(payment)
        owners = [row['provider_id'] for row in shares if row['is_current']]
        if len(owners) != 1:
            raise ValidationError('加购负责人待核对')
        owner_id = owners[0]
        from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operations
        from engagement.settlement_lifecycle import capture
        try:
            accounts = capture(order_id)
            if not {owner_id, target_id}.issubset({account.pk for account in accounts}):
                raise ValueError('加购负责人账号待核对')
            with transaction.atomic():
                lock_provider_operations(accounts)
                request._addon_handover_creation_lifecycle = (owner_id, target_id,
                    previous.pk if previous else None, {account.pk: account for account in accounts})
                return method(self, request, order_id, payment_id)
        except (ProviderLifecycleError, ValueError) as exc:
            raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc
    return wrapped


def _verify_handover_lifecycle(order_id, accounts):
    from club_accounts.models import ClubAccount
    from engagement.settlement_lifecycle import verify
    try:
        verify(order_id, list(accounts.values()))
    except ValueError as exc:
        raise ValidationError('加购接替参与人或履约来源周期待核对') from exc
    if any(not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
            license_membership_active=True, business_cycle=account.business_cycle).exists()
            for account in accounts.values()):
        raise ValidationError('加购接替参与陪玩账号周期待核对')


def _income(basis, snapshot):
    if snapshot.get('type') == 'PERCENT':
        rate = snapshot.get('rate')
        if type(rate) is not int or not 0 <= rate <= 100:
            raise ValidationError('陪玩抽成快照无效')
        fee = basis * rate // 100
    elif snapshot.get('type') == 'FIXED':
        fixed, original = snapshot.get('fixed_rule_total'), snapshot.get('order_deal_amount')
        if type(fixed) is not int or fixed < 0 or type(original) is not int or original <= 0:
            raise ValidationError('陪玩固定抽成快照无效')
        fee = min(basis, basis * fixed // original)
    else:
        raise ValidationError('陪玩抽成快照无效')
    return basis - fee


def verify_first_handover(payment, handover, *, as_of=None, receipt_id=None):
    """Verify the first handover, optionally as it stood at an earlier event."""
    from .models import OrderOriginalShareTransfer
    from .addon_payment import verify_receipt
    from .addon_offers import AddonSelectionError
    from console.models import AdminAuditLog
    verify_receipt(payment)
    order = payment.intent.order
    from .points_transfer_addon_liability import frozen_transfer_for_handover
    revision = frozen_transfer_for_handover(order, payment.pk, handover.source_segment_id,
                                             handover.target_segment_id, handover.created_at)
    liability = False
    updated_liability = False
    if (revision and isinstance(revision.snapshot, dict)
            and handover.target_segment_id == revision.transfer.next_segment_id
            and any(isinstance(row, list) and len(row) == 3 and row[0] == payment.pk
                    for row in revision.snapshot.get('pending_handover', []))):
        from .points_transfer_addon_liability import effective_at
        from .models import PointsTransferAddonLiabilityRevision
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            current = effective_at(revision, payment, handover.created_at, witness_receipt_id=receipt_id)
        except DjangoValidationError as exc:
            raise ValidationError('转单加购退款后的责任来源异常') from exc
        liability = current == [payment.pk, handover.source_segment_id, handover.source_basis_before]
        updated_liability = PointsTransferAddonLiabilityRevision.objects.filter(
            transfer_revision=revision, payment=payment, created_at__lt=handover.created_at).exists()
    retry = False
    if handover.version > 1 and not handover.previous_id:
        earlier = list(payment.handovers.filter(version__lt=handover.version).order_by('version'))
        retry = bool(liability
            and [row.version for row in earlier] == list(range(1, handover.version))
            and all(row.status in ('REJECTED', 'SUPERSEDED') and not row.accepted_at for row in earlier))
        if not retry and [row.version for row in earlier] == list(range(1, handover.version)):
            from .models import PointsHandoverRefundInvalidation
            from .points_handover_refund_invalidation import verify_current_read, verify_order_locked
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                events = [PointsHandoverRefundInvalidation.objects.select_related(
                    'handover__payment__intent', 'refund').get(handover=row) for row in earlier]
                retry = bool(events and all(row.status == 'SUPERSEDED' and not row.accepted_at for row in earlier)
                    and all(event.refund.created_at < handover.created_at
                            and (verify_current_read(event, receipt_id=receipt_id) if receipt_id
                                 else verify_order_locked(event)) for event in events))
            except (PointsHandoverRefundInvalidation.DoesNotExist, DjangoValidationError):
                retry = False
    prior = list(payment.refund_records.filter(created_at__lt=handover.accepted_at).order_by('created_at', 'pk')) if handover.accepted_at else []
    prior_basis = payment.intent.amount - sum(row.amount for row in prior)
    prior_income = payment.provider_income - sum(payment.refund_earning_deductions.filter(
        refund__created_at__lt=handover.accepted_at).values_list('amount', flat=True))
    prior_income_used = payment.refund_earning_deductions.filter(
        refund__created_at__lt=handover.accepted_at).exists()
    prior_order_refund = order.addon_refunds.filter(created_at__lt=handover.accepted_at).exists()
    pre_main_proof = None
    if prior_order_refund and not liability:
        from .points_transfer_handover_prior_refunds import facts as prior_facts
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            pre_main_proof = prior_facts(payment, handover, receipt_id=receipt_id)
        except DjangoValidationError as exc:
            raise ValidationError('接替前退款的真实资金凭证异常') from exc
    if (payment.refund_records.filter(created_at=handover.accepted_at).exists()
            or ((prior or prior_income_used or prior_order_refund) and not pre_main_proof
                and (not liability or (not updated_liability and any(
                    row.created_at >= revision.transfer.created_at for row in prior))))
            or (liability and payment.refund_earning_deductions.filter(
                refund__created_at__gte=revision.transfer.created_at,
                refund__created_at__lt=handover.accepted_at).exists() and not updated_liability)
            or prior_basis <= 0 or not 0 <= prior_income <= prior_basis):
        raise ValidationError('接替前加购退款及剩余收益凭证异常')
    if (handover.payment_id != payment.pk or handover.status != 'ACCEPTED' or not handover.accepted_at
            or (as_of is not None and handover.accepted_at >= as_of)
            or handover.previous_id or (handover.version != 1 and not retry)
            or handover.source_segment_id != payment.intent.segment_id
            or handover.source_segment.attribution.order_id != order.pk
            or handover.target_segment.attribution.order_id != order.pk
            or handover.source_segment.provider_account_id == handover.target_segment.provider_account_id
            or handover.source_basis_before != prior_basis
            or handover.retained_basis + handover.transferred_basis != handover.source_basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)
            or (handover.retained_basis and not handover.source_segment.started_at)
            or not handover.target_segment.started_at or handover.target_segment.started_at > handover.accepted_at
            or not OrderOriginalShareTransfer.objects.filter(previous_segment=handover.source_segment,
                created_at__lt=handover.accepted_at).exists()):
        raise ValidationError('加购接替履约及金额凭证异常')
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True),
                (handover.source_segment.provider_account_id, 'PROVIDER', True),
                (handover.target_segment.provider_account_id, 'PROVIDER', True)}
    if (set((account, role, accepted) for account, role, accepted, _ in decisions) != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or handover.accepted_at != max(at for _, _, _, at in decisions)):
        raise ValidationError('加购接替三方确认凭证异常')
    rows = list(handover.shares.select_related('segment__attribution').order_by('position'))
    try:
        target_snapshot = _commission(handover.target_segment, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('加购接替抽成凭证异常') from exc
    expected_rows = []
    if handover.retained_basis:
        original_income = _income(prior_basis, payment.intent.commission_snapshot)
        retained_income = (_income(handover.retained_basis, payment.intent.commission_snapshot)
            if prior_income == original_income else prior_income * handover.retained_basis // prior_basis)
        expected_rows.append((handover.source_segment_id, handover.retained_basis,
            retained_income, payment.intent.commission_snapshot, False))
    expected_rows.append((handover.target_segment_id, handover.transferred_basis,
        _income(handover.transferred_basis, target_snapshot), target_snapshot, True))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected_rows
            or [row.position for row in rows] != list(range(len(rows)))):
        raise ValidationError('加购接替各段收益凭证异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    expected_body = {'order_id': order.pk, 'payment_id': payment.pk,
        'source_provider': handover.source_segment.provider_account_id,
        'target_provider': handover.target_segment.provider_account_id,
        'retained_basis': handover.retained_basis, 'completed_work': handover.completed_work,
        'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != expected_body:
        raise ValidationError('加购接替客服审计凭证异常')
    return rows


def current_responsibilities(payment, *, lock=False, before=None, with_share_id=False, exclude_refund_id=None,
                             include_main_third=False):
    """Return effective shares, including completed stints and the active owner."""
    query = payment.handovers.filter(status='ACCEPTED')
    if before is not None:
        simultaneous_refunds = payment.refund_records.filter(created_at=before)
        simultaneous_deductions = payment.refund_earning_deductions.filter(refund__created_at=before)
        if exclude_refund_id is not None:
            simultaneous_refunds = simultaneous_refunds.exclude(pk=exclude_refund_id)
            simultaneous_deductions = simultaneous_deductions.exclude(refund_id=exclude_refund_id)
        if (payment.handovers.filter(status='ACCEPTED', accepted_at=before).exists()
                or simultaneous_refunds.exists() or simultaneous_deductions.exists()):
            raise ValidationError('加购接替与退款事件时序无法区分')
        query = query.filter(accepted_at__lt=before)
    query = query.order_by('-version')
    if lock:
        query = query.select_for_update()
    current = query.first()
    if current is None:
        intent = payment.intent
        refunds = payment.refund_records.all()
        deductions = payment.refund_earning_deductions.all()
        if before is not None:
            refunds = refunds.filter(created_at__lt=before)
            deductions = deductions.filter(refund__created_at__lt=before)
        basis = intent.amount - sum(refunds.values_list('amount', flat=True))
        income = payment.provider_income - sum(deductions.values_list('amount', flat=True))
        if include_main_third:
            from .negotiation_models import PointsNegotiationAddonDeduction
            main = PointsNegotiationAddonDeduction.objects.filter(payment=payment, addon_share__isnull=True,
                segment_id=intent.segment_id, share__account_id=intent.provider_id,
                share__posting__termination_processed_at__isnull=False)
            if before is not None:
                main = main.filter(share__posting__created_at__lt=before)
            income -= sum(main.values_list('amount', flat=True))
        if include_main_third and (basis < 0 or income < 0 or income > basis):
            raise ValidationError('主单及加购退款后的实际剩余份额异常')
        if basis <= 0:
            return None, []
        if income < 0 or income > basis:
            raise ValidationError('加购退款后的剩余收益待核对')
        return None, [{'segment_id': intent.segment_id, 'provider_id': intent.provider_id,
                       'basis': basis, 'income': income,
                       'commission_snapshot': intent.commission_snapshot, 'is_current': True}]
    shares = current.shares.select_related('segment').order_by('position')
    if (payment.refund_records.filter(created_at=current.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at=current.accepted_at).exists()):
        raise ValidationError('加购接替与退款事件时序无法区分')
    if lock:
        shares = shares.select_for_update()
    result = [{'segment_id': item.segment_id, 'provider_id': item.segment.provider_account_id,
               'basis': item.basis, 'income': item.income,
               'commission_snapshot': item.commission_snapshot, 'is_current': item.is_current,
               'share_id': item.pk}
              for item in shares]
    # The handover snapshot is immutable. Later posted refunds reduce the
    # appropriate segments without rewriting the original agreement.
    from .addon_refund_share_allocation import replay
    replay(payment, current, result, before=before, include_main_third=include_main_third)
    if not with_share_id:
        for row in result:
            del row['share_id']
    return current, [row for row in result if row['basis'] or row['income']]


def verify_later_handover(payment, handover, *, as_of=None, receipt_id=None):
    """Rebuild one later agreed split from the immediately preceding real shares."""
    from .models import OrderOriginalShareTransfer, PointsPostRefundTransferRevision
    from .addon_offers import AddonSelectionError
    from console.models import AdminAuditLog
    if (handover.payment_id != payment.pk or handover.status != 'ACCEPTED' or not handover.accepted_at
            or not handover.previous_id or not handover.created_at
            or handover.accepted_at < handover.created_at
            or (as_of is not None and handover.accepted_at >= as_of)):
        raise ValidationError('后续加购接替凭证异常')
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    if not previous or previous.pk != handover.previous_id:
        raise ValidationError('后续接替的前次负责人版本异常')
    intervening = list(payment.handovers.filter(version__gt=previous.version,
        version__lt=handover.version).order_by('version'))
    if (handover.version <= previous.version or
            [row.version for row in intervening] != list(range(previous.version + 1, handover.version))
            or any(row.status not in ('REJECTED', 'SUPERSEDED') or row.previous_id != previous.pk
                   or row.accepted_at for row in intervening)):
        raise ValidationError('后续接替重试版本异常')
    verify_handover(payment, previous, as_of=handover.created_at, receipt_id=receipt_id)
    revisions = PointsPostRefundTransferRevision.objects.filter(order=payment.intent.order,
        created_at__lt=handover.created_at).order_by('-pk')
    revision = revisions.first()
    active = [row for row in shares if row['is_current']]
    if (not revision or not isinstance(revision.snapshot, dict) or len(active) != 1
            or [payment.pk, handover.source_segment_id, handover.source_basis_before]
                not in revision.snapshot.get('pending_handover', [])
            or revision.transfer.previous_segment_id != handover.source_segment_id
            or revision.transfer.next_segment_id != handover.target_segment_id
            or active[0]['segment_id'] != handover.source_segment_id
            or active[0]['basis'] != handover.source_basis_before
            or handover.source_segment.provider_account_id == handover.target_segment.provider_account_id
            or handover.target_segment.attribution.order_id != payment.intent.order_id
            or not handover.target_segment.started_at or handover.target_segment.started_at > handover.accepted_at
            or not OrderOriginalShareTransfer.objects.filter(pk=revision.transfer_id,
                created_at__lt=handover.created_at).exists()
            or handover.retained_basis + handover.transferred_basis != handover.source_basis_before
            or handover.transferred_basis <= 0 or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('后续加购接替与转单负债不一致')
    if (payment.refund_records.filter(created_at__gte=handover.created_at, created_at__lte=handover.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                refund__created_at__lte=handover.accepted_at).exists()
            or payment.handovers.filter(status='ACCEPTED', accepted_at__gt=previous.accepted_at,
                accepted_at__lt=handover.accepted_at).exclude(pk=handover.pk).exists()):
        raise ValidationError('后续接替期间加购责任已变化')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True),
                (handover.source_segment.provider_account_id, 'PROVIDER', True),
                (handover.target_segment.provider_account_id, 'PROVIDER', True)}
    if (set((account, role, accepted) for account, role, accepted, _ in decisions) != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('后续加购接替三方确认异常')
    try:
        target_snapshot = _commission(handover.target_segment, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('后续接替抽成凭证异常') from exc
    source = active[0]
    expected_shares = [row for row in shares if not row['is_current']]
    if handover.retained_basis:
        full_income = _income(source['basis'], source['commission_snapshot'])
        retained_income = (_income(handover.retained_basis, source['commission_snapshot'])
            if source['income'] == full_income else source['income'] * handover.retained_basis // source['basis'])
        expected_shares.append({**source, 'basis': handover.retained_basis,
                                'income': retained_income, 'is_current': False})
    expected_shares.append({'segment_id': handover.target_segment_id,
        'provider_id': handover.target_segment.provider_account_id, 'basis': handover.transferred_basis,
        'income': _income(handover.transferred_basis, target_snapshot),
        'commission_snapshot': target_snapshot, 'is_current': True})
    rows = list(handover.shares.select_related('segment__attribution').order_by('position'))
    if ([row.position for row in rows] != list(range(len(rows))) or
            [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] !=
            [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], row['is_current'])
             for row in expected_shares]):
        raise ValidationError('后续接替历史份额不一致')
    path = f'/api/admin/orders/{payment.intent.order_id}/addons/payments/{payment.pk}/handovers/'
    expected_body = {'order_id': payment.intent.order_id, 'payment_id': payment.pk,
        'source_provider': handover.source_segment.provider_account_id,
        'target_provider': handover.target_segment.provider_account_id,
        'retained_basis': handover.retained_basis, 'completed_work': handover.completed_work,
        'remaining_work': handover.remaining_work}
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=path, method='POST', status_code=200))
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != expected_body:
        raise ValidationError('后续接替客服审计凭证异常')
    return rows


def verify_handover(payment, handover, *, as_of=None, receipt_id=None):
    from .points_voluntary_handover_event import KIND, provisional
    temporary = provisional(handover)
    if temporary is not None:
        if handover.payment_id != payment.pk or (as_of is not None and handover.accepted_at >= as_of):
            raise ValidationError('自愿接替确认上下文不匹配')
        return temporary
    run = getattr(handover, 'funding_release_run', None)
    if run:
        from .models import PointsThirdAddonFundingRevision
        revision = PointsThirdAddonFundingRevision.objects.filter(run=run, handover=handover,
            order_id=payment.intent.order_id).first()
        if revision:
            if handover.payment_id != payment.pk or (as_of is not None and handover.accepted_at >= as_of):
                raise ValidationError('第三轮后接替资金事件不匹配')
            from .points_third_addon_receipt_usage import verify_run_locked, verify_run_receipt
            from .points_business_access import _bind
            order = payment.intent.order
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                if receipt_id is None:
                    verify_run_locked(run)
                else:
                    verify_run_receipt(run, receipt_id)
            return list(handover.shares.select_related('segment__attribution').order_by('position'))
    if run and isinstance(run.points_handover_snapshot, dict) and run.points_handover_snapshot.get('kind') == 'SAME_TEAM_V1':
        if handover.payment_id != payment.pk or (as_of is not None and handover.accepted_at >= as_of):
            raise ValidationError('同单接替资金事件不匹配')
        from .addon_funding_release import verify_run
        verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        return list(handover.shares.select_related('segment__attribution').order_by('position'))
    if run and isinstance(run.points_followup_snapshot, dict) and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1':
        if handover.payment_id != payment.pk or (as_of is not None and handover.accepted_at >= as_of):
            raise ValidationError('后续退款接替资金事件不匹配')
        from .points_transfer_followup_handover_scope import verify_event
        from .addon_funding_release import verify_run
        rows = verify_event(payment, handover, before=as_of, receipt_id=receipt_id)
        verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        return rows
    if run and isinstance(run.points_handover_snapshot, dict) and run.points_handover_snapshot.get('kind') == KIND:
        if handover.payment_id != payment.pk or (as_of is not None and handover.accepted_at >= as_of):
            raise ValidationError('自愿接替资金事件不匹配')
        from .addon_funding_release import verify_run
        verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        return list(handover.shares.select_related('segment__attribution').order_by('position'))
    verifier = verify_later_handover if handover.previous_id else verify_first_handover
    return verifier(payment, handover, as_of=as_of, receipt_id=receipt_id)


def assert_no_pending_handover(payment):
    """Coordinate settlement or refund with an unfinished change of responsibility."""
    if payment.handovers.filter(status='PENDING').exists():
        raise ValidationError('加购负责人变更正在等待确认，请先处理')


def _verify_transferred_handover_replay(order, payment, proposal):
    from .addon_funding_release import verify_run
    from .models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation

    if not proposal.accepted_at:
        raise ValidationError('加购接替缺少确认时间，请先核对')
    funded = OrderPointsFundingAllocation.objects.filter(
        redemption__order=order, created_at__lte=proposal.accepted_at).exists()
    run = OrderAddonFundingReleaseRun.objects.filter(handover=proposal).first()
    if funded and (not run or not run.points_handover_snapshot or not run.points_handover_hash):
        raise ValidationError('加购接替资金凭证缺失，请先核对')
    if run:
        verify_run(run)
    else:
        verify_handover(payment, proposal)


def _payment_locked(order_id, payment_id, *, funding_first=False):
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('订单不存在')
    if funding_first:
        # The final decision may recalculate funding and lock payment sources.
        # Serialize it before those locks and take every receipt in the same
        # order as the other order-money writers, including on retries.
        from django.db.models import Q
        from users.models import PassRefundRepaymentReceipt
        from .financial_lock import lock_order_money
        from .models import OrderPointsFundingAllocation
        lock_order_money()
        ids = set(OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)
        ).values_list('receipt_id', flat=True))
        if ids:
            receipts = list(PassRefundRepaymentReceipt.objects.select_for_update()
                            .filter(pk__in=ids).order_by('pk'))
            if len(receipts) != len(ids):
                raise ValidationError('订单到账凭证缺失，请先核对')
    payment = (OrderAddonPayment.objects.select_for_update().select_related('intent__segment')
               .filter(pk=payment_id, intent__order=order).first())
    if not payment:
        raise ValidationError('加购付款不存在')
    return order, payment


def _active_target(order, account_id, *, pending_handover=None):
    from django.db.models import Q
    from django.conf import settings
    allowed = Q(attribution__status='ACTIVE')
    from .ordinary_refunded_team_handover import permits as ordinary_team_permits
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    from .points_business_access import permits as points_permits
    points_round = (order.refund_records.count() >= 4
                    and points_permits(order, phase='handover', kind='transfer_addon'))
    if (points_round or getattr(settings, 'ORDINARY_REFUNDED_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING', False)
            or ordinary_team_permits(order)
            or ordinary_transfer_permits(order, allow_addon_handover=True, allow_addon_refund=True)):
        allowed |= Q(attribution__status='REVIEW', attribution__review_reason='协商退款后原价份额待核对')
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        allowed, attribution__order=order, provider_account_id=account_id,
        released_at__isnull=True, started_at__isnull=False, financial_snapshot__isnull=False).select_related('attribution'))
    if len(segments) != 1 or not OrderProvider.objects.select_for_update().filter(
            order=order, provider_account_id=account_id, released_at__isnull=True).exists():
        raise ValidationError('接替人须是主单当前正在履约的陪玩')
    if segments[0].attribution.status == 'REVIEW':
        _verify_refunded_target(order, segments[0], pending_handover=pending_handover)
    return segments[0]


def _verify_refunded_target(order, segment, *, pending_handover=None):
    """Only the verified ordinary negotiation may use the reviewed split."""
    from .models import OrderOriginalShareTransfer, OrderPrestartHandoff, OrderSegmentFinancialCoverage
    from .negotiation_settlement import execution_for_order
    from .negotiation_execution import verify_execution
    from .negotiation_termination import verify_termination_evidence
    from .points_service_payment import needs_integrated_processing
    from .settlement import SettlementError, compute_provider_income
    try:
        from .points_business_access import permits as points_permits
        if (order.refund_records.count() >= 4
                and points_permits(order, phase='handover', kind='transfer_addon')):
            from .points_round_funding import verify_active_segment_locked
            verify_active_segment_locked(order, segment)
            return
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if transferred:
            from .ordinary_transfer_capability import permits
            from .ordinary_post_refund_transfer import current_locked
            if not permits(order, allow_addon_handover=True, allow_addon_refund=True):
                raise ValidationError('转单后的加购接替范围待核对')
            revision = current_locked(order, ordinary_pending_handover_id=(
                pending_handover.pk if pending_handover else None))
            coverage = OrderSegmentFinancialCoverage.objects.select_for_update().get(attribution=segment.attribution)
            lines = revision.snapshot.get('lines', []) if revision else []
            if (not revision or segment.attribution.status != 'REVIEW'
                    or segment.attribution.review_reason != '协商退款后原价份额待核对'
                    or coverage.status != 'REVIEW' or coverage.review_reason != '协商退款后原价份额待核对'
                    or coverage.source != 'WALLET_NO_POINTS' or coverage.deal_amount != order.amount
                    or sum(1 for line in lines if line.get('segment_id') == segment.pk
                           and line.get('account_id') == segment.provider_account_id) != 1):
                raise ValidationError('转入陪玩的资金版本或履约段待核对')
            return
        execution = execution_for_order(order, lock=True)
        if (needs_integrated_processing(order) or order.refund_records.count() != 1 or not execution
                or not execution.refund_id or execution.refund.order_id != order.pk
                or OrderPrestartHandoff.objects.filter(order=order).exists()):
            raise ValidationError('主单退款及履约范围待核对')
        verify_execution(execution)
        verify_termination_evidence(execution)
        assignment = OrderProvider.objects.select_for_update().get(order=order, provider_account_id=segment.provider_account_id)
        snapshot = segment.financial_snapshot
        coverage = OrderSegmentFinancialCoverage.objects.select_for_update().get(attribution=segment.attribution)
        if (segment.attribution.status != 'REVIEW' or segment.attribution.review_reason != '协商退款后原价份额待核对'
                or coverage.status != 'REVIEW' or coverage.review_reason != '协商退款后原价份额待核对'
                or coverage.source != 'WALLET_NO_POINTS' or coverage.deal_amount != order.amount
                or snapshot.retained_basis is not None or snapshot.incoming_basis != assignment.settlement_base
                or snapshot.commission_type != assignment.commission_type or snapshot.commission_rate != assignment.commission_rate
                or snapshot.commission_fixed != assignment.commission_fixed or snapshot.income_at_entry != assignment.provider_income
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot
                or assignment.provider_income != compute_provider_income(assignment.settlement_base, assignment.commission_type,
                                                                           assignment.commission_rate, assignment.commission_fixed)):
            raise ValidationError('接替陪玩的原收益快照待核对')
    except (SettlementError, OrderProvider.DoesNotExist, OrderSegmentFinancialCoverage.DoesNotExist) as exc:
        raise ValidationError('主单退款及接替陪玩凭证待核对') from exc


def _target_commission(segment, *, pending_handover=None):
    from .addon_offers import AddonSelectionError
    try:
        if segment.attribution.status == 'REVIEW':
            _verify_refunded_target(segment.attribution.order, segment, pending_handover=pending_handover)
            return _commission(segment, allow_refund_review=True)
        return _commission(segment)
    except AddonSelectionError as exc:
        raise ValidationError(str(exc)) from exc


def _eligible(order, payment, *, affected_accounts=()):
    from .points_voluntary_handover_event import entry_matches
    voluntary = entry_matches(order, payment.pk)
    refunded_handover = False
    if order.payment_status == 'REFUNDED' and order.status == Order.Status.IN_SERVICE:
        from .points_transfer_followup_handover_scope import refunded_reproposal_active
        refunded_handover = refunded_reproposal_active(order, payment)
        from .points_transfer_followup_addon_version import handover_preflight_active
        pending = list(payment.handovers.filter(status='PENDING', kind='STANDARD')[:2])
        if not refunded_handover and len(pending) == 1 and handover_preflight_active(order, pending[0]):
            from .models import OrderPointsTransferFollowupFundingRevision
            revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
                posting__points_refund__redemption__order=order).first()
            snapshot = revision.pending_handover_snapshot if revision else None
            refunded_handover = bool(isinstance(snapshot, list) and any(isinstance(item, dict)
                and item.get('handover_id') == pending[0].pk and item.get('payment_id') == payment.pk
                for item in snapshot))
    if (order.status != Order.Status.IN_SERVICE or
            (order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED') and not (
                (voluntary or refunded_handover) and order.payment_status == 'REFUNDED'))):
        raise ValidationError('仅进行中的主单可以更换加购负责人')
    if voluntary and affected_accounts:
        from .models import OrderSegmentPersonSettlement, OrderSegmentAddonSettlement
        if (order.providers.filter(provider_account_id__in=affected_accounts, settled_at__isnull=False).exists()
                or OrderSegmentPersonSettlement.objects.select_for_update().filter(
                    manifest__order=order, account_id__in=affected_accounts).exists()
                or OrderSegmentAddonSettlement.objects.select_for_update().filter(
                    manifest__order=order, segment__provider_account_id__in=affected_accounts).exists()):
            raise ValidationError('已结算人员不能参与加购责任变更')
    if payment.settlements.exists() or payment.segment_settlements.exists():
        raise ValidationError('已结算加购不能更换负责人')
    if payment.completion_requests.filter(status='ACCEPTED').exists():
        raise ValidationError('加购已确认全部完成，不能更换负责人')
    from .models import OrderOriginalShareTransfer
    ordinary_transfer = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).exists()
    if (payment.refund_proposals.filter(status__in=('PENDING', 'ACCEPTED'), refund__isnull=True).exists()
            and not ordinary_transfer):
        raise ValidationError('加购退款方案尚未处理，请先完成售后')
    if affected_accounts:
        if ordinary_transfer:
            from .ordinary_pending_refund_scope import independent
            if not independent(order, changed_payment_ids={payment.pk}, changed_accounts=set(affected_accounts),
                               allow_same_payment=True):
                raise ValidationError('其他退款方案涉及本次接替责任，请先完成或重新确认')
    for case in order.aftersales.filter(status='OPEN'):
        if not any(applies_to(case, account_id) for account_id in affected_accounts):
            continue
        # A separately agreed add-on refund can coexist only while its
        # frozen ordinary plan remains current; a changed plan is invalidated
        # by the accepting transaction before funding is recalculated.
        if (ordinary_transfer
                and case.addon_refund_proposals.filter(status__in=('PENDING', 'ACCEPTED'),
                    refund__isnull=True, cross_pending_plan__version='ORDINARY_TRANSFER_V1').exists()):
            continue
        raise ValidationError('受影响陪玩仍有未结售后，请先处理')


def _data(handover, *, account=None):
    decisions = list(handover.decisions.order_by('pk'))
    shares = handover.shares.select_related('segment').order_by('position')
    # Prior, unaffected stints stay in the ledger but are not disclosed to other players.
    visible = {handover.source_segment.provider_account_id, handover.target_segment.provider_account_id}
    return {'id': handover.pk, 'payment_id': handover.payment_id, 'version': handover.version, 'kind': handover.kind,
            'source_provider_account_id': handover.source_segment.provider_account_id,
            'target_provider_account_id': handover.target_segment.provider_account_id,
            'source_basis_before_raw': handover.source_basis_before,
            'retained_basis_raw': handover.retained_basis, 'transferred_basis_raw': handover.transferred_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work,
            'shares': [{'segment_id': row.segment_id, 'provider_account_id': row.segment.provider_account_id,
                        'basis_raw': row.basis, 'income_raw': row.income, 'is_current': row.is_current}
                       for row in shares if account is None or account.account_type == 'STAFF'
                       or row.segment.provider_account_id in visible],
            'decisions': [{'account_id': row.account_id, 'role': row.role, 'accepted': row.accepted,
                           'decided_at': row.decided_at} for row in decisions],
            'status': handover.status, 'created_at': handover.created_at, 'accepted_at': handover.accepted_at,
            'settlement_available': False}


def _fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class StaffAddonHandoverView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, order_id, payment_id):
        if not {'order:view', 'order:dispatch'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看加购负责人变更'}, status=403)
        rows = OrderAddonHandover.objects.select_related('source_segment', 'target_segment').filter(
            payment_id=payment_id, payment__intent__order_id=order_id).order_by('-version')
        return Response({'code': 0, 'data': [_data(row, account=request.account) for row in rows]})

    @_handover_creation_lifecycle
    @transaction.atomic
    @_ordinary_funding_scope
    @_points_handover_scope
    def post(self, request, order_id, payment_id):
        if 'order:dispatch' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权安排加购负责人'}, status=403)
        body = request.data
        if not isinstance(body, Mapping) or set(body) not in ({
                'request_id', 'target_provider_account_id', 'retained_basis_raw', 'completed_work', 'remaining_work'}, {
                'request_id', 'target_provider_account_id', 'retained_basis_raw', 'completed_work', 'remaining_work', 'kind'}):
            raise ValidationError('请填写接替人、完成内容和收益分配')
        kind = body.get('kind', 'STANDARD')
        if kind not in ('STANDARD', 'VOLUNTARY'):
            raise ValidationError('接替类型无效')
        request_id, target_id, retained = body['request_id'], body['target_provider_account_id'], body['retained_basis_raw']
        completed, remaining = body['completed_work'], body['remaining_work']
        if (not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 64
                or type(target_id) is not int or target_id <= 0 or type(retained) is not int or retained < 0
                or not isinstance(completed, str) or len(completed.strip()) > 500
                or not isinstance(remaining, str) or not 2 <= len(remaining.strip()) <= 500
                or (retained and len(completed.strip()) < 2)):
            raise ValidationError('请填写有效的分配金额和实际履约内容')
        order, payment = _payment_locked(order_id, payment_id, funding_first=True)
        body_hash = _fingerprint(dict(body))
        previous_request = payment.handovers.filter(request_id=request_id.strip()).first()
        if previous_request:
            if previous_request.created_by_id != request.account.pk or previous_request.request_hash != body_hash:
                raise ValidationError('请求编号已用于其他换人方案')
            return Response({'code': 0, 'data': _data(previous_request, account=request.account)})
        from .ordinary_transfer_capability import permits as transferred_permits
        from .ordinary_refunded_team_handover import permits as ordinary_team_permits
        from .ordinary_team_pre_main_handover import permits as pre_main_team_permits
        from .ordinary_pending_handover_refund import verify_retry as verify_ordinary_retry
        from .ordinary_team_later_handover import can_start as can_start_later_team
        from .ordinary_zero_post_addon import handover_permitted as zero_handover_permitted
        zero_team = zero_handover_permitted(order, payment)
        ordinary_team = (ordinary_team_permits(order) and verify_ordinary_retry(payment)
                         and not order.providers.filter(settled_at__isnull=False).exists())
        pre_main_team = (pre_main_team_permits(order)
                         and (verify_ordinary_retry(payment) or can_start_later_team(payment))
                         and not order.providers.filter(settled_at__isnull=False).exists())
        if (not enabled() and not transferred_permits(order, allow_addon_handover=True, allow_addon_refund=True)
                and not ordinary_team and not pre_main_team and not zero_team
                and not getattr(request, '_points_team_handover_replay', False)):
            raise ValidationError('加购负责人变更尚未开放')
        from .points_business_access import permits as points_permits
        from .points_voluntary_handover_event import entry_matches
        third_scope = bool(order.refund_records.count() >= 3 and
            points_permits(order, phase='handover', kind='transfer_addon'))
        third_voluntary = third_scope and kind == 'VOLUNTARY'
        voluntary = third_voluntary or entry_matches(order, payment.pk)
        if (kind == 'VOLUNTARY') != voluntary:
            raise ValidationError('自愿接替不属于当前积分订单范围')
        if points_permits(order, phase='handover', kind='addon'):
            from .models import OrderOriginalShareTransfer, PointsHandoverRefundInvalidation
            pending = OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
            invalidated_retry = (not voluntary and not OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).exists()
                and all(PointsHandoverRefundInvalidation.objects.filter(handover=row, refund__order=order).exists()
                        for row in payment.handovers.filter(status='SUPERSEDED')))
            allowed = ('REJECTED', 'ACCEPTED', 'SUPERSEDED') if invalidated_retry else ('REJECTED', 'ACCEPTED')
            if ((not voluntary and not third_scope and payment.handovers.exclude(status__in=allowed).exists()) or
                    (pending.filter(payment=payment).exists() if voluntary or third_scope else pending.exists())):
                raise ValidationError('该加购已有接替方案或其他接替正在等待确认')
        _eligible(order, payment)
        from .addon_payment import verify_receipt
        verify_receipt(payment)
        if third_scope:
            from .points_third_addon_responsibilities import current as third_responsibilities
            prior, shares = third_responsibilities(payment, lock=True)
        else:
            prior, shares = current_responsibilities(payment, lock=True)
        current = [item for item in shares if item['is_current']]
        refundable = payment.intent.amount - sum(payment.refund_records.values_list('amount', flat=True))
        if not refundable:
            raise ValidationError('该加购已全部退款，没有可接替的内容')
        if len(current) != 1 or sum(item['basis'] for item in shares) != refundable:
            raise ValidationError('加购负责人或历史份额待核对')
        source = current[0]
        lifecycle = getattr(request, '_addon_handover_creation_lifecycle', None)
        if lifecycle:
            owner_id, captured_target_id, previous_id, accounts = lifecycle
            _verify_handover_lifecycle(order.pk, accounts)
            if (source['provider_id'] != owner_id or target_id != captured_target_id
                    or (prior.pk if prior else None) != previous_id):
                raise ValidationError('加购负责人已变化，请刷新后重试')
            from club_accounts.provider_lifecycle import provider_membership_current
            if not provider_membership_current(accounts[target_id]):
                raise ValidationError('接替陪玩账号状态已变化，请重新安排')
        if target_id == source['provider_id']:
            raise ValidationError('接替人须与当前负责人不同')
        if retained >= source['basis']:
            raise ValidationError('接替份额须大于零')
        target = _active_target(order, target_id)
        if zero_team:
            from .negotiation_zero import marked_execution_id
            from .models import NegotiationExecution
            first = NegotiationExecution.objects.get(pk=marked_execution_id(order), zero_order=order)
            if (source['provider_id'] not in first.proposal.affected_accounts
                    or target.provider_account_id in first.proposal.affected_accounts):
                raise ValidationError('仅可将已终止陪玩的加购交给继续履约陪玩')
        _eligible(order, payment, affected_accounts=(source['provider_id'], target_id))
        from .points_transfer_refund_access import permits as transfer_permits
        if (transfer_permits(order, phase='transfer') and not order.refund_records.exists()
                and order.addon_refunds.exists()):
            from .models import OrderPointsFundingAllocation, OrderOriginalShareTransfer, OrderAddonFundingReleaseRun
            from .points_addon_receipt_usage import facts as refund_facts
            from .addon_funding_release import verify_run
            from django.core.exceptions import ValidationError as DjangoValidationError
            transfer = OrderOriginalShareTransfer.objects.filter(previous_segment_id=source['segment_id'],
                previous_segment__attribution__order=order).order_by('-created_at', '-pk').first()
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order,
                receipt__usage_version__isnull=False).order_by('pk').first()
            refunds = list(order.addon_refunds.select_related('payment__intent', 'proposal').order_by('created_at', 'pk'))
            accepted = list(OrderAddonHandover.objects.filter(payment__intent__order=order,
                status='ACCEPTED').order_by('accepted_at', 'pk'))
            from .points_transfer_pending_handover_events import is_unaffected
            if (not order.addon_intents.exists()
                    or OrderAddonPayment.objects.filter(intent__order=order).count() != order.addon_intents.count()
                    or not transfer
                    or len({row.created_at for row in refunds}) != len(refunds)
                    or any(row.created_at == transfer.created_at for row in refunds)
                    or any(not item.accepted_at for item in accepted)
                    or any(item.created_at <= row.created_at <= item.accepted_at and not is_unaffected(row, item)
                           for row in refunds for item in accepted)):
                raise ValidationError('接替前退款的资金或事件范围尚未接通')
            if not witness:
                from .points_transfer_refund_evidence import _facts_without_receipt_locked
                _facts_without_receipt_locked(order)
                if any(row.created_at < transfer.created_at for row in refunds):
                    from .points_transfer_handover_prior_refunds import verify_zero_transfer_revision
                    try:
                        verify_zero_transfer_revision(order, transfer)
                    except DjangoValidationError as exc:
                        raise ValidationError('转单前退款的空资金版本异常') from exc
            for refunded in refunds:
                run = OrderAddonFundingReleaseRun.objects.filter(refund=refunded).first()
                if not run or not isinstance(run.points_addon_snapshot, dict):
                    raise ValidationError('接替前退款缺少真实释放凭证')
                try:
                    refund_facts(refunded, read_receipt_id=witness.receipt_id if witness else None)
                    verify_run(run, _skip_usage=True, witness_receipt_id=witness.receipt_id if witness else None)
                except DjangoValidationError as exc:
                    raise ValidationError('接替前退款或释放凭证异常') from exc
        if (points_permits(order, phase='handover', kind='addon') and order.refund_records.exists()
                and not voluntary and not third_scope):
            from .points_team_postmain_handover_facts import require_source
            require_source(order, source['segment_id'], target.pk)
        if points_permits(order, phase='handover', kind='addon'):
            from .models import OrderProviderCompletionFact
            from django.db.models import Q
            finished = Q(provider_account_id=target_id, segment_id=target.pk)
            if not (third_scope and kind == 'STANDARD'):
                finished |= Q(provider_account_id=source['provider_id'], segment_id=source['segment_id'])
            if OrderProviderCompletionFact.objects.filter(order=order).filter(finished).exists():
                raise ValidationError('已结束本人履约的陪玩不能接替加购')
        target_snapshot = _target_commission(target)
        latest = payment.handovers.select_for_update().order_by('-version').first()
        if latest and latest.status == 'PENDING':
            latest.status = 'SUPERSEDED'
            latest.save(update_fields=['status'])
            from site_messages.addon_handover_proposal_notice import refresh_delivered
            refresh_delivered(latest)
        version = latest.version + 1 if latest else 1
        transfer = source['basis'] - retained
        proposal = OrderAddonHandover.objects.create(payment=payment, previous=prior, version=version, kind=kind,
            request_id=request_id.strip(), request_hash=body_hash, created_by=request.account,
            source_segment_id=source['segment_id'], target_segment=target, source_basis_before=source['basis'],
            retained_basis=retained, transferred_basis=transfer, completed_work=completed.strip(),
            remaining_work=remaining.strip())
        new_shares = [item for item in shares if not item['is_current']]
        if retained:
            original_income = _income(source['basis'], source['commission_snapshot'])
            retained_income = (_income(retained, source['commission_snapshot']) if source['income'] == original_income
                               else source['income'] * retained // source['basis'])
            new_shares.append({**source, 'basis': retained, 'income': retained_income,
                               'is_current': False})
        new_shares.append({'segment_id': target.pk, 'provider_id': target.provider_account_id,
                           'basis': transfer, 'income': _income(transfer, target_snapshot),
                           'commission_snapshot': target_snapshot, 'is_current': True})
        if ((pre_main_team or ordinary_team)
                and any(not item['basis'] or item['income'] > item['basis'] for item in new_shares)):
            raise ValidationError('加购接替的剩余份额收益须先核对')
        OrderAddonHandoverShare.objects.bulk_create([
            OrderAddonHandoverShare(handover=proposal, position=index, segment_id=item['segment_id'],
                basis=item['basis'], income=item['income'], commission_snapshot=item['commission_snapshot'],
                is_current=item['is_current']) for index, item in enumerate(new_shares)])
        OrderAddonHandoverDecision.objects.bulk_create([
            OrderAddonHandoverDecision(handover=proposal, account_id=payment.intent.buyer_id, role='BOSS'),
            OrderAddonHandoverDecision(handover=proposal, account_id=source['provider_id'], role='PROVIDER'),
            OrderAddonHandoverDecision(handover=proposal, account_id=target_id, role='PROVIDER'),
        ])
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='addon-handover', object_id=str(proposal.pk),
            request_body={'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source['provider_id'],
                          'target_provider': target_id, 'retained_basis': retained,
                          'completed_work': completed.strip(), 'remaining_work': remaining.strip()}, status_code=200)
        request._request._business_audit_recorded = True
        from site_messages.addon_handover_proposal_notice import record_all as record_proposal_notices
        record_proposal_notices(proposal)
        from .notifier import notify_order_update
        transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code': 0, 'data': _data(proposal, account=request.account)})


class UserAddonHandoverListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id, payment_id):
        rows = OrderAddonHandover.objects.select_related('source_segment', 'target_segment').filter(
            payment_id=payment_id, payment__intent__order_id=order_id,
            decisions__account=request.account).order_by('-version')
        return Response({'code': 0, 'data': [_data(row, account=request.account) for row in rows]})


class UserAddonHandoverDecisionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @_handover_lifecycle
    @transaction.atomic
    @_ordinary_funding_scope
    @_points_handover_scope
    def post(self, request, order_id, payment_id, handover_id):
        if not isinstance(request.data, Mapping) or set(request.data) != {'accepted'} or type(request.data['accepted']) is not bool:
            raise ValidationError('请选择同意或拒绝')
        replay_only = getattr(request, '_points_transferred_handover_replay', False)
        # The replay scope already holds the order, money mutex and receipt locks.
        order, payment = _payment_locked(order_id, payment_id, funding_first=not replay_only)
        from .ordinary_transfer_capability import permits as transferred_permits
        from .ordinary_refunded_team_handover import permits as ordinary_team_permits
        from .ordinary_team_pre_main_handover import permits as pre_main_team_permits
        proposal = (payment.handovers.select_for_update().select_related('source_segment', 'target_segment')
                    .filter(pk=handover_id, decisions__account=request.account).first())
        if proposal is None:
            return Response({'code': 404, 'msg': '换人方案不存在'}, status=404)
        decision = proposal.decisions.select_for_update().get(account=request.account)
        accepted = request.data['accepted']
        ordinary_team = ordinary_team_permits(order)
        pre_main_team = pre_main_team_permits(order)
        from .ordinary_zero_post_addon import handover_permitted as zero_handover_permitted
        zero_team = zero_handover_permitted(order, payment)
        from .points_transfer_refund_access import permits as points_transfer_permits
        if replay_only and (proposal.status != 'ACCEPTED' or decision.accepted is not True
                or not points_transfer_permits(order, phase='report') or order.refund_records.exists()):
            raise ValidationError('原加购接替确认状态已变化，请先核对')
        if (not replay_only and not enabled()
                and not transferred_permits(order, allow_addon_handover=True, allow_addon_refund=True)
                and not ordinary_team and not pre_main_team and not zero_team):
            if proposal.status != 'ACCEPTED' or decision.accepted is None or decision.accepted != accepted:
                raise ValidationError('加购负责人变更尚未开放')
            first_refund = order.refund_records.order_by('created_at', 'pk').first()
            if first_refund and proposal.accepted_at < first_refund.created_at:
                from .ordinary_team_pre_main_handover import verify as verify_pre_main_handover
                verify_pre_main_handover(proposal)
                from .models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation
                if OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=proposal.accepted_at).exists():
                    run = OrderAddonFundingReleaseRun.objects.filter(handover=proposal).first()
                    if not run or not run.ordinary_prestart_snapshot or not run.ordinary_prestart_hash:
                        raise ValidationError('加购接替资金凭证缺失')
                    from .addon_funding_release import verify_run
                    verify_run(run)
            else:
                from .ordinary_refunded_team_handover import verify as verify_team_handover
                verify_team_handover(proposal)
        if decision.accepted is not None:
            if decision.accepted != accepted:
                raise ValidationError('确认已记录，请联系客服修改方案')
            if (proposal.status == 'ACCEPTED' and (replay_only or points_transfer_permits(order, phase='transfer'))
                    and not order.refund_records.exists()):
                _verify_transferred_handover_replay(order, payment, proposal)
            if proposal.status == 'ACCEPTED' and zero_team:
                from .ordinary_refunded_team_handover import verify as verify_zero_handover
                from .models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation
                verify_zero_handover(proposal)
                run = OrderAddonFundingReleaseRun.objects.filter(handover=proposal).first()
                if not run and OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=proposal.accepted_at).exists():
                    raise ValidationError('加购接替资金凭证缺失，请先核对')
                if run:
                    from .addon_funding_release import verify_run
                    verify_run(run)
            from .points_business_access import permits as points_permits
            if proposal.status == 'ACCEPTED' and (points_permits(order, phase='handover', kind='addon')
                    or (getattr(request, '_points_team_handover_replay', False)
                        and points_permits(order, phase='report', kind='addon'))):
                from .models import OrderAddonFundingReleaseRun
                from .addon_funding_release import verify_run
                run = OrderAddonFundingReleaseRun.objects.filter(handover=proposal).first()
                if not run:
                    raise ValidationError('加购接替资金凭证缺失，请先核对')
                if order.refund_records.count() >= 3 and points_permits(
                        order, phase='handover', kind='transfer_addon'):
                    verify_handover(payment, proposal)
                else:
                    verify_run(run)
            return Response({'code': 0, 'data': _data(proposal, account=request.account)})
        lifecycle = getattr(request, '_addon_handover_lifecycle', None)
        if lifecycle:
            snapshot, accounts = lifecycle
            _verify_handover_lifecycle(order.pk, accounts)
            if snapshot != (proposal.source_segment.provider_account_id, proposal.target_segment.provider_account_id):
                raise ValidationError('换人方案的陪玩已变化，请刷新后重试')
        provider_existing = bool(lifecycle and request.account.pk in lifecycle[1])
        if (proposal.status != 'PENDING' or (not provider_existing
                and (not request.account.is_active or not request.account.can_login))):
            raise ValidationError('方案已结束或账号不可用')
        if accepted:
            _eligible(order, payment, affected_accounts=(proposal.source_segment.provider_account_id,
                                                          proposal.target_segment.provider_account_id))
            from .points_business_access import permits as points_permits
            if points_permits(order, phase='handover', kind='addon'):
                from .models import OrderProviderCompletionFact
                from django.db.models import Q
                from .points_business_access import permits as _third_permits
                target_only = bool(proposal.kind == 'STANDARD' and order.refund_records.count() >= 3
                                   and _third_permits(order, phase='handover', kind='transfer_addon'))
                finished = Q(provider_account_id=proposal.target_segment.provider_account_id,
                             segment_id=proposal.target_segment_id)
                if not target_only:
                    finished |= Q(provider_account_id=proposal.source_segment.provider_account_id,
                                  segment_id=proposal.source_segment_id)
                if OrderProviderCompletionFact.objects.filter(order=order).filter(finished).exists():
                    raise ValidationError('已结束本人履约的陪玩不能确认加购接替')
            from .points_business_access import permits as _points_scope_permits
            third_handover = bool(order.refund_records.count() >= 3 and
                _points_scope_permits(order, phase='handover', kind='transfer_addon'))
            if third_handover:
                from .points_third_addon_responsibilities import current as third_responsibilities
                prior, shares = third_responsibilities(payment, lock=True)
            else:
                prior, shares = current_responsibilities(payment, lock=True)
            source = next((item for item in shares if item['is_current']), None)
            target = _active_target(order, proposal.target_segment.provider_account_id, pending_handover=proposal)
            if (points_permits(order, phase='handover', kind='addon') and order.refund_records.exists()
                    and proposal.kind != 'VOLUNTARY' and not third_handover):
                from .points_team_postmain_handover_facts import require_source
                require_source(order, proposal.source_segment_id, target.pk)
            target_share = proposal.shares.get(is_current=True)
            if ((prior.pk if prior else None) != proposal.previous_id or not source
                    or source['segment_id'] != proposal.source_segment_id or source['basis'] != proposal.source_basis_before
                    or target.pk != proposal.target_segment_id or target_share.commission_snapshot != _target_commission(
                        target, pending_handover=proposal)):
                raise ValidationError('加购负责人或抽成依据已变化，请重新确认')
        third_capture = None
        if (accepted and order.refund_records.count() >= 3 and
                _points_scope_permits(order, phase='handover', kind='transfer_addon') and
                not proposal.decisions.filter(accepted__isnull=True).exclude(pk=decision.pk).exists()):
            from .points_third_addon_handover import capture_locked
            third_capture = capture_locked(order, payment)
        decision.accepted, decision.decided_at = accepted, timezone.now()
        decision.save(update_fields=['accepted', 'decided_at'])
        if not accepted:
            proposal.status = 'REJECTED'
        elif not proposal.decisions.filter(accepted__isnull=True).exists():
            proposal.status, proposal.accepted_at = 'ACCEPTED', decision.decided_at
        proposal.save(update_fields=['status', 'accepted_at'])
        if proposal.status == 'ACCEPTED':
            from .ordinary_transfer_capability import permits as ordinary_transfer_permits
            if ordinary_transfer_permits(order, allow_addon_handover=True, allow_addon_refund=True):
                from .addon_refund_cross_plan import invalidate_changed_ordinary
                invalidate_changed_ordinary(order, changed_payments={payment.pk},
                    changed_accounts={proposal.source_segment.provider_account_id,
                                      proposal.target_segment.provider_account_id},
                    event_kind='ADDON_HANDOVER', event_id=proposal.pk, actor=request.account,
                    legacy_actor=getattr(request, 'legacy_user', None))
            if third_capture is not None:
                from .points_third_addon_handover import record_locked
                record_locked(order, proposal, third_capture)
            elif proposal.kind == 'VOLUNTARY':
                from .points_voluntary_handover_event import accepting, entry_matches
                if not entry_matches(order, payment.pk):
                    raise ValidationError('自愿接替确认不属于当前资金事务')
                from .addon_funding_release import record_handover_funding_release
                with accepting(proposal):
                    record_handover_funding_release(order, proposal)
            elif ordinary_team or zero_team:
                from .ordinary_refunded_team_handover import accepting
                from .ordinary_refund_addon_payment_release import record_locked
                if zero_team:
                    from .models import NegotiationExecution
                    from .negotiation_zero import marked_execution_id
                    from .ordinary_zero_post_addon import verify_addon_refunds_before
                    from .points_funding_evidence import verified_order_allocations_locked
                    first = NegotiationExecution.objects.get(pk=marked_execution_id(order), zero_order=order)
                    allocations = verified_order_allocations_locked(order)
                    witness = allocations[0].receipt_id if allocations else None
                    verify_addon_refunds_before(order, first, proposal.created_at, witness_receipt_id=witness)
                with accepting(proposal):
                    record_locked(order, handover=proposal)
            elif pre_main_team:
                from .ordinary_refunded_team_handover import accepting
                from .ordinary_team_pre_main_handover import verify as verify_pre_main_handover
                from .addon_funding_release import record_handover_funding_release
                with accepting(proposal):
                    verify_pre_main_handover(proposal)
                    record_handover_funding_release(order, proposal)
            else:
                from .addon_funding_release import record_handover_funding_release
                from .ordinary_post_refund_transfer import refresh_for_event
                from .points_transfer_followup_addon_version import handover_preflight_active
                paid_before = None
                if handover_preflight_active(order, proposal):
                    from .points_followup_paid_handover import capture_before_locked
                    paid_before = capture_before_locked(order, proposal)
                if not refresh_for_event(order, source_kind='ADDON_HANDOVER', source_id=proposal.pk):
                    record_handover_funding_release(order, proposal)
                if paid_before is not None:
                    from .points_followup_paid_handover import record_locked, transition_in_service_locked
                    evidence = record_locked(order, proposal, paid_before)
                    transition_in_service_locked(order, evidence)
        from site_messages.addon_handover_proposal_notice import refresh_delivered
        refresh_delivered(proposal)
        from site_messages.addon_handover_notice import record_all as record_handover_notices
        record_handover_notices(proposal)
        from .notifier import notify_order_update
        transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code': 0, 'data': _data(proposal, account=request.account), 'msg': '确认已记录'})
