"""Conservative reconciliation of the directory and one tenant's pass repayments."""
import json

from django.db import DatabaseError, transaction
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.context import store_context
from stores.db import transaction as tenant_transaction
from users.models import (PassRefundRepaymentAction, PassRefundRepaymentAllocation,
                          PassRefundRepaymentReceipt, ProviderPassRefund)
from users.pass_repayment import points_funding_table_exists
from .hosts import is_admin_host
from .models import PlatformAuditEvent, PlatformPassRepaymentClaim, PlatformPassRepaymentOperation, PlatformPointsFundingOperation
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_pass_repayment import (RepaymentError, _checked_action, _claimed_receipt, _has_pending,
                                      _locked_finance, _points_operation_table_exists, _sha)


def _pages(query, *, chronological=None):
    """Read the complete query in its original stable order, in small pages."""
    cursor = None
    while True:
        current = query
        if chronological is None:
            if cursor is not None:
                current = current.filter(pk__gt=cursor)
            current = current.order_by('pk')
        elif chronological:
            if cursor is not None:
                current = current.filter(Q(created_at__gt=cursor[0])
                                         | Q(created_at=cursor[0], pk__gt=cursor[1]))
            current = current.order_by('created_at', 'pk')
        else:
            if cursor is not None:
                current = current.filter(Q(created_at__lt=cursor[0])
                                         | Q(created_at=cursor[0], pk__lt=cursor[1]))
            current = current.order_by('-created_at', '-pk')
        page = list(current[:256])
        if not page:
            return
        yield from page
        last = page[-1]
        cursor = last.pk if chronological is None else (last.created_at, last.pk)


def read(token, slug):
    with transaction.atomic(using='default'):
        actor, store = _locked_finance(token, slug)
        claims_qs = PlatformPassRepaymentClaim.objects.filter(store=store).order_by('created_at', 'id')
        ops_qs = PlatformPassRepaymentOperation.objects.filter(claim__store=store).select_related('claim').order_by('created_at', 'id')
        points_qs = (PlatformPointsFundingOperation.objects.filter(claim__store=store).select_related('claim').order_by('created_at', 'id')
                     if _points_operation_table_exists() else None)
        claims, operations = list(_pages(claims_qs, chronological=True)), list(_pages(ops_qs, chronological=True))
        points_operations = list(_pages(points_qs, chronological=True)) if points_qs is not None else []
        status = 'PENDING' if _has_pending(store) else 'OK'
        rows, refunds, receipts_by_id, verified_results = [], [], {}, {}
        with store_context(store), tenant_transaction.atomic():
            receipt_qs = PassRefundRepaymentReceipt.objects.all()
            allocation_qs = PassRefundRepaymentAllocation.objects.all()
            action_qs = PassRefundRepaymentAction.objects.select_related('allocation')
            refund_qs = ProviderPassRefund.objects.filter(shortfall_amount__gt=0).order_by('-created_at', '-id')
            tenant_receipts = {r.pk: r for r in _pages(receipt_qs)}
            # Load both uses of a receipt, including independent tenant action
            # evidence. A migrated allocation table can precede its action
            # table during rollout; do not infer a balance in that state.
            point_allocations, point_actions = {}, {}
            if points_funding_table_exists():
                from orders.models import OrderPointsFundingAction, OrderPointsFundingAllocation
                allocation_points_qs = OrderPointsFundingAllocation.objects.select_related('redemption')
                point_allocations = {a.pk: a for a in _pages(allocation_points_qs)}
                connection = tenant_transaction.get_connection()
                with connection.cursor() as cursor:
                    action_table_exists = OrderPointsFundingAction._meta.db_table in connection.introspection.table_names(cursor)
                if action_table_exists:
                    action_points_qs = OrderPointsFundingAction.objects.all()
                    point_actions = {a.pk: a for a in _pages(action_points_qs)}
                else:
                    from django.db.migrations.recorder import MigrationRecorder
                    from stores.context import current_database
                    action_migration_applied = MigrationRecorder.Migration.objects.using(current_database()).filter(
                        app='orders', name='0125_orderpointsfundingaction').exists()
                    if action_migration_applied or point_allocations or points_operations:
                        status = 'REVIEW'
            elif points_operations:
                status = 'REVIEW'
            allocations = {a.pk: a for a in _pages(allocation_qs)}
            actions = {a.pk: a for a in _pages(action_qs)}
            actions_by_key = {a.request_key: a for a in actions.values()}
            seen_receipts, seen_actions, seen_allocations = set(), set(), set()
            pending_claim_keys = {f'platform:{c.pk.hex}' for c in claims if c.tenant_receipt_id is None and not c.voided_at}
            for claim in claims:
                row = {'claim_id': str(claim.pk), 'request_id': str(claim.request_id),
                       'created_at': claim.created_at.isoformat(), 'status': 'PENDING', 'receipt': None}
                if claim.voided_at:
                    row['status'] = 'VOIDED'
                    row['void_reason'] = claim.void_reason
                elif claim.tenant_receipt_id is None:
                    status = 'PENDING' if status == 'OK' else status
                else:
                    try:
                        receipt = _claimed_receipt(claim, slug, tenant_receipts.get(claim.tenant_receipt_id))
                        if receipt.pk in seen_receipts:
                            raise RepaymentError('重复关联')
                        seen_receipts.add(receipt.pk)
                        receipts_by_id[receipt.pk] = receipt
                        row.update(status='RECORDED', receipt={'id': receipt.pk,
                            'external_reference': receipt.external_reference, 'amount': receipt.amount,
                            'received_at': receipt.received_at.isoformat(), 'evidence': receipt.evidence,
                            'registered_by': receipt.registered_by, 'verified_by': receipt.verified_by or None,
                            'verified_at': receipt.verified_at.isoformat() if receipt.verified_at else None,
                            'allocated_amount': None, 'unallocated_amount': None})
                    except RepaymentError:
                        status, row['status'] = 'REVIEW', 'REVIEW'
                rows.append(row)
            # Unlinked rows are not silently treated as zero. A pending claim
            # may legitimately have committed its tenant receipt already.
            if any(r.pk not in seen_receipts and r.request_key not in pending_claim_keys for r in tenant_receipts.values()):
                status = 'REVIEW'
            for op in operations:
                if op.rejected_at or op.voided_at:
                    if f'platform:{op.pk.hex}' in actions_by_key:
                        status = 'REVIEW'
                    continue
                if op.tenant_action_id is None:
                    if status == 'OK': status = 'PENDING'
                    continue
                receipt = receipts_by_id.get(op.claim.tenant_receipt_id)
                action = actions.get(op.tenant_action_id)
                if not receipt or not action or action.pk in seen_actions:
                    status = 'REVIEW'; continue
                refund_id = action.allocation.refund_id if action.allocation_id else None
                amount = action.allocation.amount if action.allocation_id else None
                digest = _sha(json.dumps([str(op.claim_id), op.kind, refund_id, amount], separators=(',', ':')))
                try:
                    checked = _checked_action(op, receipt, op.kind, refund_id, amount, action)
                    if not checked or checked.pk != action.pk or digest != op.payload_digest:
                        raise RepaymentError('操作不一致')
                except RepaymentError:
                    status = 'REVIEW'; continue
                seen_actions.add(action.pk)
                verified_results[op.pk] = action.result
                if action.allocation_id:
                    seen_allocations.add(action.allocation_id)
            pending_op_keys = {f'platform:{op.pk.hex}' for op in operations if not op.rejected_at and not op.voided_at and op.tenant_action_id is None}
            if any(a.pk not in seen_actions and a.request_key not in pending_op_keys for a in actions.values()):
                status = 'REVIEW'
            pending_allocations = {a.allocation_id for a in actions.values() if a.request_key in pending_op_keys}
            if any(a.pk not in seen_allocations and a.pk not in pending_allocations for a in allocations.values()):
                status = 'REVIEW'
            point_actions_by_key = {action.request_key: action for action in point_actions.values()}
            point_allocations_by_key = {allocation.request_key: allocation for allocation in point_allocations.values()}
            seen_point_actions, seen_point_allocations = set(), set()
            pending_point_keys = set()
            for op in points_operations:
                key = f'points:{op.pk.hex}'
                action = point_actions_by_key.get(key)
                allocation = point_allocations.get(action.allocation_id) if action else None
                if op.rejected_at or op.voided_at:
                    if action or key in point_allocations_by_key:
                        status = 'REVIEW'
                    continue
                if op.tenant_action_id is None:
                    pending_point_keys.add(key)
                    if status == 'OK': status = 'PENDING'
                    continue
                receipt = receipts_by_id.get(op.claim.tenant_receipt_id)
                if not action or not allocation or not receipt or action.pk != op.tenant_action_id:
                    status = 'REVIEW'; continue
                expected = {'receipt_id': receipt.pk, 'allocation_id': allocation.pk,
                            'order_id': allocation.funding_order_id, 'amount': allocation.amount}
                digest = _sha(json.dumps([str(op.claim_id), allocation.funding_order_id, allocation.amount],
                                         separators=(',', ':')))
                if (action.pk in seen_point_actions or allocation.pk in seen_point_allocations
                        or action.actor != str(op.actor_id) or allocation.allocated_by != str(op.actor_id)
                        or allocation.request_key != key or allocation.receipt_id != receipt.pk
                        or action.result != expected or op.payload_digest != digest
                        or not receipt.verified_at or not receipt.verified_by):
                    status = 'REVIEW'; continue
                seen_point_actions.add(action.pk)
                seen_point_allocations.add(allocation.pk)
                verified_results[op.pk] = expected
            pending_point_allocation_ids = {a.allocation_id for a in point_actions.values()
                                            if a.request_key in pending_point_keys}
            if any(a.pk not in seen_point_actions and a.request_key not in pending_point_keys
                   for a in point_actions.values()):
                status = 'REVIEW'
            if any(a.pk not in seen_point_allocations and a.pk not in pending_point_allocation_ids
                   for a in point_allocations.values()):
                status = 'REVIEW'
            verified_receipt_ids = {a.receipt_id for a in actions.values() if a.kind == 'VERIFY'}
            if any(r.verified_at and r.pk not in verified_receipt_ids for r in receipts_by_id.values()):
                status = 'REVIEW'
            totals, receipt_totals = {}, {}
            for allocation in allocations.values():
                totals[allocation.refund_id] = totals.get(allocation.refund_id, 0) + allocation.amount
                receipt_totals[allocation.receipt_id] = receipt_totals.get(allocation.receipt_id, 0) + allocation.amount
                linked_receipt = tenant_receipts.get(allocation.receipt_id)
                if not linked_receipt or not linked_receipt.verified_at: status = 'REVIEW'
            for allocation in point_allocations.values():
                linked_receipt = tenant_receipts.get(allocation.receipt_id)
                if not linked_receipt or not linked_receipt.verified_at: status = 'REVIEW'
            from orders.points_funding_release import receipt_points_used_locked
            from rest_framework.exceptions import ValidationError as RestValidationError
            for receipt_id in {allocation.receipt_id for allocation in point_allocations.values()}:
                try:
                    # This is a read snapshot, not authorization to allocate;
                    # actual writers re-read while holding the receipt mutex.
                    receipt = tenant_receipts[receipt_id]
                    if receipt.usage_version is not None:
                        # The shared ledger below also includes pass and
                        # referral purposes and their individual releases.
                        continue
                    used = receipt_points_used_locked(receipt)
                    receipt_totals[receipt_id] = receipt_totals.get(receipt_id, 0) + used
                except (RestValidationError, DatabaseError, KeyError):
                    status = 'REVIEW'
            from users.receipt_usage_guard import isolated_allocation_state_locked
            for receipt in tenant_receipts.values():
                if receipt.usage_version is None:
                    continue
                try:
                    state = isolated_allocation_state_locked(receipt)
                    if not state or state.pass_units != receipt_totals.get(receipt.pk, 0):
                        status = 'REVIEW'
                        continue
                    receipt_totals[receipt.pk] = (state.pass_units + state.order_units + state.referral_units
                                                  - state.released_units - state.referral_released_units)
                except (ValidationError, RestValidationError, DatabaseError):
                    status = 'REVIEW'
            refund_ids = set()
            for refund in _pages(refund_qs, chronological=False):
                refund_ids.add(refund.pk)
                repaid = totals.get(refund.pk, 0)
                if repaid > refund.shortfall_amount: status = 'REVIEW'
                refunds.append({'refund_id': refund.pk, 'shortfall_amount': refund.shortfall_amount,
                                'allocated_amount': repaid, 'remaining_amount': refund.shortfall_amount - repaid})
            if set(totals) - refund_ids: status = 'REVIEW'
            for row in rows:
                receipt = row['receipt']
                if receipt:
                    used = receipt_totals.get(receipt['id'], 0)
                    if used > receipt['amount']: status = 'REVIEW'
                    receipt['allocated_amount'] = used
                    receipt['unallocated_amount'] = receipt['amount'] - used if receipt['verified_at'] else None
            # Any unresolved operation can affect a refund without revealing
            # its target in the directory, so no remaining balance is asserted.
            if status != 'OK':
                for row in rows:
                    if row['receipt']:
                        row['receipt']['allocated_amount'] = row['receipt']['unallocated_amount'] = None
                for row in refunds:
                    row['allocated_amount'] = row['remaining_amount'] = None
        # Do not upgrade a long-running read transaction to a write. SQLite
        # cannot safely upgrade concurrent readers, and the snapshot should be
        # released promptly before recording the read audit.
        transaction.on_commit(lambda: PlatformAuditEvent.objects.create(account=actor,
            action='pass_repayment.read_reconciliation', target_store_slug=slug,
            target_type='store', target_id=str(store.pk), detail={'status': status}), using='default')
        return {'slug': slug, 'currency_name': store.currency_name, 'status': status,
                'claims': rows, 'refunds': refunds,
                'points_operations': [{'operation_id': str(op.pk), 'request_id': str(op.request_id),
                    'claim_id': str(op.claim_id), 'actor_id': str(op.actor_id), 'created_at': op.created_at.isoformat(),
                    'status': 'VOIDED' if op.voided_at else 'REJECTED' if op.rejected_at else
                              'COMPLETED' if op.completed_at else 'PENDING',
                    'result': verified_results.get(op.pk),
                    'rejection_message': op.void_reason or op.rejection_message or None} for op in points_operations],
                'operations': [{'operation_id': str(op.pk), 'request_id': str(op.request_id), 'claim_id': str(op.claim_id),
                    'kind': op.kind, 'status': 'VOIDED' if op.voided_at else 'REJECTED' if op.rejected_at else 'COMPLETED' if op.completed_at else 'PENDING',
                    'created_at': op.created_at.isoformat(), 'actor_id': str(op.actor_id),
                    'result': verified_results.get(op.pk), 'rejection_message': op.void_reason or op.rejection_message or None}
                    for op in operations], 'read_at': timezone.now().isoformat()}


class PlatformPassRepaymentReconciliationView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug):
        from django.conf import settings
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
            return Response({'code': 503, 'msg': '补款核销暂未开放'}, status=503)
        try:
            data = read(request.auth, slug)
        except RepaymentError as error:
            return Response({'code': error.status, 'errorCode': error.code, 'msg': str(error)}, status=error.status)
        except DatabaseError:
            return Response({'code': 503, 'errorCode': 'REPAYMENT_UNAVAILABLE', 'msg': '核对数据暂不可用'}, status=503)
        response = Response({'code': 0, 'data': data})
        response['Cache-Control'] = 'no-store'
        return response
