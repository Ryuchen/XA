"""Participant-specific entry for a provider joining an in-progress order."""
import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from wallet.models import ProviderReport
from .models import Order, OrderOriginalAttribution, OrderParticipationSegment, OrderProvider
from .order_operation_receipts import order_receipt


def pending_transfer_segments(order, *, account_id=None):
    from .models import OrderWorkStint
    work = OrderWorkStint.objects.filter(order=order, incoming__isnull=False,
        started_at__isnull=True, released_at__isnull=True)
    if not OrderOriginalAttribution.objects.filter(order=order).exists():
        return work.filter(account_id=account_id) if account_id is not None else work
    query = OrderParticipationSegment.objects.filter(attribution__order_id=order.pk,
        attribution__status='ACTIVE', released_at__isnull=True, started_at__isnull=True,
        original_transfer_in__isnull=False)
    return query.filter(provider_account_id=account_id) if account_id is not None else query


def _fingerprint(request, order_id):
    image = request.FILES.get('entry_image')
    digest = hashlib.sha256()
    if image:
        for chunk in image.chunks():
            digest.update(chunk)
        image.seek(0)
    else:
        digest.update(b'no-image')
    value = f'{order_id}:{request.data.get("segment_id", "")}:{request.data.get("work_stint_id", "")}:{digest.hexdigest()}'
    return hashlib.sha256(value.encode()).hexdigest()


class TransferParticipationStartView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @order_receipt('order.transfer-participation', 'start', _fingerprint)
    def post(self, request, order_id):
        from .original_attribution import enabled as original_attribution_enabled
        if ('work_stint_id' in request.data or ('segment_id' not in request.data
                and not original_attribution_enabled())):
            return self._independent_start(request, order_id)
        if request.account.account_type != 'PROVIDER':
            return Response({'code': 403, 'msg': '仅接替陪玩本人可以确认入队'}, status=403)
        try:
            segment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('segment_id'))
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '请选择当前转入记录'})
        image = request.FILES.get('entry_image')
        if settings.REQUIRE_ORDER_EVIDENCE_IMAGES and not image:
            return Response({'code': 400, 'msg': '请上传本次入队截图'})
        if image:
            try:
                serializers.ImageField().run_validation(image)
            except (serializers.ValidationError, DjangoValidationError):
                return Response({'code': 400, 'msg': '入队截图必须为有效图片'})
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code': 404, 'msg': '订单不存在'}, status=404)
            if order.payment_status != Order.PaymentStatus.PAID:
                from rest_framework.exceptions import ValidationError
                from .ordinary_transfer_continuation import verify_locked
                try:
                    verify_locked(order)
                except (ValidationError, ValueError):
                    return Response({'code': 409, 'msg': '协商退款后的履约凭证待核对'}, status=409)
            attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=order).first()
            if not attribution or attribution.status != 'ACTIVE':
                return Response({'code': 409, 'msg': '原价参与记录待核对，请联系客服'}, status=409)
            segments = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution=attribution, provider_account=request.account, released_at__isnull=True))
            if len(segments) != 1 or segments[0].pk != segment_id:
                return Response({'code': 409, 'msg': '本次转入记录已变化，请刷新订单'}, status=409)
            segment = segments[0]
            if not hasattr(segment, 'original_transfer_in'):
                return Response({'code': 400, 'msg': '该记录不是服务中转入'})
            if order.status != Order.Status.IN_SERVICE:
                return Response({'code': 409, 'msg': '订单状态已变化，请刷新后核对'}, status=409)
            assignment = OrderProvider.objects.filter(order=order, provider_account=request.account,
                                                       released_at__isnull=True).first()
            if not assignment or not request.legacy_user or assignment.provider_id != request.legacy_user.pk:
                return Response({'code': 403, 'msg': '仅本次转入的陪玩可以确认入队'}, status=403)
            # New orders maintain both the money-independent work interval and
            # the authoritative original-price segment under this order lock.
            # Bind the two to the same committed seat transfer before writing.
            from .models import OrderOriginalShareTransfer, OrderWorkStint
            work = None
            if OrderWorkStint.objects.filter(order=order).exists():
                transfer = OrderOriginalShareTransfer.objects.select_related('previous_segment').filter(
                    next_segment=segment).first()
                rows = list(OrderWorkStint.objects.select_for_update().filter(order=order,
                    account=request.account, released_at__isnull=True, incoming__isnull=False)[:2])
                if len(rows) != 1 or not transfer:
                    return Response({'code': 409, 'msg': '本次履约与原价记录待核对'}, status=409)
                work = rows[0]
                event = work.incoming
                previous_work = list(OrderWorkStint.objects.filter(order=order, account_id=event.old_account_id,
                    released_at=transfer.created_at)[:2])
                if (event.new_account_id != segment.provider_account_id
                        or event.old_account_id != transfer.previous_segment.provider_account_id
                        or event.seat.order_id != order.pk or event.created_at > transfer.created_at
                        or transfer.created_at != segment.accepted_at
                        or len(previous_work) != 1
                        or previous_work[0].started_at != transfer.previous_segment.started_at
                        or event.seat.current_account_id != request.account.pk
                        or event.sequence != event.seat.transfer_count
                        or bool(work.started_at) != bool(segment.started_at)
                        or (work.started_at and work.started_at != segment.started_at)):
                    return Response({'code': 409, 'msg': '本次履约与原价记录待核对'}, status=409)
            if segment.started_at is None:
                report, _ = ProviderReport.objects.get_or_create(order=order, provider=request.legacy_user,
                    defaults={'provider_account':request.account, 'game_name':order.service_name_snapshot,
                              'description':order.remark, 'amount':assignment.settlement_base, 'status':'DRAFT'})
                if report.provider_account_id and report.provider_account_id != request.account.pk:
                    return Response({'code': 409, 'msg': '报单归属待核对，请联系客服'}, status=409)
                segment.started_at = timezone.now(); segment.start_source = 'TRANSFER_ENTRY'
                if image:
                    segment.entry_image = image
                segment.save(update_fields=['started_at', 'start_source', 'entry_image'])
                if work:
                    work.started_at = segment.started_at
                    work.entry_image = segment.entry_image.name if segment.entry_image else None
                    work.save(update_fields=['started_at', 'entry_image'])
                if not report.entry_image and segment.entry_image and report.status == ProviderReport.Status.DRAFT:
                    report.entry_image = segment.entry_image.name
                    if not report.provider_account_id:
                        report.provider_account = request.account
                        report.save(update_fields=['entry_image', 'provider_account', 'updated_at'])
                    else:
                        report.save(update_fields=['entry_image', 'updated_at'])
                from site_messages.service_transfer_notice import record_all as record_transfer_notices
                record_transfer_notices(segment.original_transfer_in)
                from .notifier import notify_order_update
                transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        return Response({'code': 0, 'msg': '本次入队已记录', 'data': OrderSerializer(order, context={'request':request}).data})

    def _independent_start(self, request, order_id):
        from .models import OrderWorkStint
        if request.account.account_type != 'PROVIDER':
            return Response({'code': 403, 'msg': '仅接替陪玩本人可以确认入队'}, status=403)
        try:
            stint_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('work_stint_id'))
            image = request.FILES.get('entry_image')
            if settings.REQUIRE_ORDER_EVIDENCE_IMAGES and not image:
                return Response({'code': 400, 'msg': '请上传本次入队截图'})
            if image:
                serializers.ImageField().run_validation(image)
        except (serializers.ValidationError, DjangoValidationError):
            return Response({'code': 400, 'msg': '请选择本次转入并上传有效入队截图'})
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code': 404, 'msg': '订单不存在'}, status=404)
            stint = OrderWorkStint.objects.select_for_update().select_related('incoming__seat').filter(
                pk=stint_id, order=order, account=request.account, released_at__isnull=True,
                incoming__isnull=False).first()
            if (not stint or stint.incoming.seat.current_account_id != request.account.pk
                    or stint.incoming.sequence != stint.incoming.seat.transfer_count):
                return Response({'code': 409, 'msg': '本次转入记录已变化，请刷新订单'}, status=409)
            if order.status != Order.Status.IN_SERVICE or order.payment_status != Order.PaymentStatus.PAID:
                return Response({'code': 409, 'msg': '订单状态已变化，请刷新后核对'}, status=409)
            assignment = OrderProvider.objects.filter(order=order, provider_account=request.account,
                                                       released_at__isnull=True).first()
            if not assignment or not request.legacy_user or assignment.provider_id != request.legacy_user.pk:
                return Response({'code': 403, 'msg': '仅本次转入的陪玩可以确认入队'}, status=403)
            if stint.started_at is None:
                report, _ = ProviderReport.objects.get_or_create(order=order, provider=request.legacy_user,
                    defaults={'provider_account': request.account, 'game_name': order.service_name_snapshot,
                              'description': order.remark, 'amount': assignment.settlement_base, 'status': 'DRAFT'})
                if report.provider_account_id and report.provider_account_id != request.account.pk:
                    return Response({'code': 409, 'msg': '报单归属待核对，请联系客服'}, status=409)
                stint.started_at = timezone.now()
                if image:
                    stint.entry_image = image
                stint.save(update_fields=['started_at', 'entry_image'])
                if not report.entry_image and stint.entry_image and report.status == ProviderReport.Status.DRAFT:
                    report.entry_image = stint.entry_image.name
                    report.provider_account = request.account
                    report.save(update_fields=['entry_image', 'provider_account', 'updated_at'])
                from .notifier import notify_order_update
                transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        return Response({'code': 0, 'msg': '本次入队已记录', 'data': OrderSerializer(order, context={'request': request}).data})


def _completion_fingerprint(request, order_id):
    image = request.FILES.get('completion_image')
    digest = hashlib.sha256()
    if image:
        for chunk in image.chunks():
            digest.update(chunk)
        image.seek(0)
    return hashlib.sha256(f'{order_id}:{request.data.get("segment_id", "")}:{digest.hexdigest()}'.encode()).hexdigest()


def negotiated_current_completion_allowed(order, segment):
    """Only the verified, completed negotiated segment can upload after termination."""
    from .subsidy_transfer_capability import stage_enabled
    if (not stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING')
            or order.status != Order.Status.COMPLETED or order.settled_at or segment.released_at
            or not segment.started_at):
        return False
    from .models import SubsidyAgreementExecution, SubsidyTransferAgreementTerms
    from .subsidy_execution import table_available, verify_execution
    from .subsidy_transfer_agreement_evidence import _tables_ready
    from rest_framework.exceptions import ValidationError
    if not table_available() or not _tables_ready():
        return False
    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            from .financial_lock import lock_order_money
            lock_order_money()
            execution = SubsidyAgreementExecution.objects.select_for_update().select_related(
                'order', 'proposal__case').filter(order=locked).first()
            if not execution:
                return False
            verify_execution(execution)
            return SubsidyTransferAgreementTerms.objects.filter(proposal_id=execution.proposal_id,
                segment_id=segment.pk, account_id=segment.provider_account_id).exists()
    except (ValidationError, Order.DoesNotExist, TypeError, ValueError):
        return False


class TransferParticipationCompletionEvidenceView(APIView):
    """Attach evidence to a released segment while its report is editable."""
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @order_receipt('order.segment-completion-evidence', 'evidence', _completion_fingerprint)
    def post(self, request, order_id):
        from .original_attribution import enabled as original_attribution_enabled
        if request.account.account_type != 'PROVIDER':
            return Response({'code':403, 'msg':'仅本人可上传履约凭证'}, status=403)
        try:
            segment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('segment_id'))
            image = serializers.ImageField().run_validation(request.FILES.get('completion_image'))
        except (serializers.ValidationError, DjangoValidationError):
            return Response({'code':400, 'msg':'请选择本人履约记录并上传有效结单截图'})
        from contextlib import ExitStack
        from django.conf import settings
        with transaction.atomic(), ExitStack() as scopes:
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code':404, 'msg':'订单不存在'}, status=404)
            if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
                from .points_service_payment import needs_integrated_processing
                from .models import OrderOriginalShareTransfer
                if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists():
                    from .points_transfer_refund_access import after_transfer
                    order = scopes.enter_context(after_transfer(order.pk, phase='report'))
            if not original_attribution_enabled():
                from .principal_segments import enabled_for_order
                if not enabled_for_order(order):
                    return Response({'code':404, 'msg':'当前不支持记录履约凭证'})
            segment = OrderParticipationSegment.objects.select_for_update().filter(pk=segment_id,
                attribution__order=order, attribution__status='ACTIVE', provider_account=request.account,
                started_at__isnull=False).first()
            negotiated_current = bool(segment and not segment.released_at and negotiated_current_completion_allowed(order, segment))
            if not segment or (not segment.released_at and not negotiated_current):
                return Response({'code':404, 'msg':'本人已转出的履约记录不存在'}, status=404)
            report = ProviderReport.objects.select_for_update().filter(order=order, provider_account=request.account).first()
            from .services import report_edit_error
            from .segment_reports import supports as segment_reports_support
            segment_report = None
            segment_supported = segment_reports_support(order)
            if segment_supported:
                from .models import OrderSegmentReport
                segment_report = OrderSegmentReport.objects.select_for_update().filter(segment=segment).first()
            if segment_report and segment_report.status not in ('DRAFT', 'REJECTED'):
                return Response({'code':409, 'msg':'本段报单已提交，请先撤回或等待审核'}, status=409)
            from .points_service_payment import needs_integrated_processing
            from .points_transfer_capture import enabled as points_transfer_enabled
            isolated_points_segment = segment_supported and points_transfer_enabled() and needs_integrated_processing(order)
            error = '' if segment_report or isolated_points_segment or negotiated_current else (report_edit_error(report) if report else '请先创建本人订单报单')
            if error:
                return Response({'code':400, 'msg':error})
            previous_image = segment.completion_image.name if segment.completion_image else ''
            segment.completion_image = image
            segment.save(update_fields=['completion_image'])
            # Preserve the existing single-report workflow only when there is
            # exactly one started participation for this person. Multiple
            # entries must not be silently merged into one legacy evidence.
            count = OrderParticipationSegment.objects.filter(attribution__order=order,
                provider_account=request.account, started_at__isnull=False).count()
            if (report and report.status in ('DRAFT', 'REJECTED') and count == 1
                    and (not report.completion_image or report.completion_image.name == previous_image)):
                report.completion_image = segment.completion_image.name
                report.save(update_fields=['completion_image', 'updated_at'])
            from .notifier import notify_order_update
            transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        return Response({'code':0, 'msg':'本次履约结单截图已保存', 'data':OrderSerializer(order, context={'request':request}).data})
