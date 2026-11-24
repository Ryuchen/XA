"""Per-participation report evidence and review; never posts wallet income."""
import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.db.models import Q
from rest_framework import serializers
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import get_account_permissions, is_console_account
from stores.db import transaction
from wallet.report_lifecycle import provider_report_operation
from .models import (Order, OrderParticipationSegment, OrderSegmentReport, OrderSegmentReportImage,
                     OrderSegmentReportSubmission, OrderSegmentSubmissionImage)
from .order_operation_receipts import order_receipt


def enabled():
    from .segment_financial_capture import enforcement_enabled
    from .original_attribution import enabled as original_enabled
    return bool(getattr(settings, 'SEGMENT_REPORT_ENABLED', False)
                and original_enabled() and enforcement_enabled())


def supports(order):
    from .segment_financial_capture import transfer_income_pending
    from .ordinary_transfer_capability import enabled as ordinary_transfer_enabled
    from .points_service_payment import needs_integrated_processing
    if (ordinary_transfer_enabled() and order.status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            and order.payment_status in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED,
                                         Order.PaymentStatus.REFUNDED)
            and not (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                     and needs_integrated_processing(order))):
        from rest_framework.exceptions import ValidationError
        from .models import NegotiationExecution, OrderTransferNegotiationEvidence
        from .ordinary_transfer_capability import permits
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                from .financial_lock import lock_order_money
                lock_order_money()
                if not permits(locked) or not OrderTransferNegotiationEvidence.objects.filter(order=locked).exists():
                    return False
                from .negotiation_execution import verify_execution
                from .negotiation_termination import verify_termination_evidence
                receipts = list(NegotiationExecution.objects.select_for_update().filter(
                    Q(refund__order=locked) | Q(zero_order=locked), termination_processed_at__isnull=False).order_by('pk'))
                if sum(bool(receipt.refund_id) for receipt in receipts) != locked.refund_records.count():
                    return False
                for receipt in receipts:
                    if receipt.refund_id:
                        verify_execution(receipt)
                        verify_termination_evidence(receipt)
                    else:
                        from .negotiation_zero import verify_zero_execution as verify_zero, verify_zero_termination as verify_zero_end
                        verify_zero(receipt)
                        verify_zero_end(receipt)
                from .segment_settlement import _post_refund_transfer_version
                if _post_refund_transfer_version(locked):
                    from .ordinary_post_refund_transfer import current_locked
                    current_locked(locked)
            return True
        except (ValidationError, ValueError, Order.DoesNotExist):
            return False
    if (enabled() and order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
            and getattr(settings, 'ORDINARY_TRANSFER_REFUND_REPORT_INTERNAL_TESTING', False)):
        from rest_framework.exceptions import ValidationError
        from .negotiation_transfer_preview import _require_isolated
        from .negotiation_settlement import execution_for_order
        from .negotiation_execution import verify_execution
        from .negotiation_termination import verify_termination_evidence
        try:
            _require_isolated()
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                from .financial_lock import lock_existing_order_money
                lock_existing_order_money()
                execution = execution_for_order(locked, lock=True)
                if (not execution or not execution.refund_id or not execution.termination_processed_at
                        or execution.refund.benefit_effects.get('ordinary_transfer_negotiation_version') != 1):
                    return False
                verify_execution(execution)
                verify_termination_evidence(execution)
            return True
        except (ValidationError, Order.DoesNotExist):
            return False
    from .points_transfer_refund_access import permits as transfer_permits
    verified_transfer = transfer_permits(order, phase='report')
    if (enabled() and order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
            and (verified_transfer or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING', False))):
        if verified_transfer:
            # after_transfer already verified the closure under this order's locks.
            return 1 <= order.refund_records.count() <= 2
        from rest_framework.exceptions import ValidationError
        from .points_transfer_refund_continuation import verify_locked
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                if locked.refund_records.count() == 2:
                    if not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False):
                        return False
                    from .models import OrderPointsTransferFollowupFundingRevision
                    from .points_transfer_followup_revision import _verify_followup_revision_facts_locked
                    revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
                        posting__points_refund__redemption__order=locked).first()
                    if not revision:
                        return False
                    _verify_followup_revision_facts_locked(revision)
                else:
                    verify_locked(locked)
            return True
        except (ValidationError, Order.DoesNotExist):
            return False
    from .principal_segments import enabled_for_order
    return bool((enabled() or enabled_for_order(order)) and order.status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
                and order.payment_status == Order.PaymentStatus.PAID and transfer_income_pending(order))


def supports_detail(order):
    """Check reporting on a single authorized order detail."""
    supported, _ = detail_support(order)
    return supported


def detail_support(order, *, check_paid_transfer=False):
    """Return report support and eligibility to upload released points segments."""
    from rest_framework.exceptions import ValidationError
    from .models import OrderOriginalShareTransfer
    from .points_business_access import formal_service_read_candidate
    from .points_transfer_refund_access import after_transfer, permits

    if permits(order, phase='report'):
        supported = supports(order)
        return supported, supported
    refunded = order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
    if (not (refunded or (check_paid_transfer and order.payment_status == Order.PaymentStatus.PAID))
            or not enabled()
            or not formal_service_read_candidate(order, phase='report')
            or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return supports(order), False
    try:
        with transaction.atomic(), after_transfer(order.pk, phase='report', read_only=True) as locked:
            supported = supports(locked)
            return supported, supported
    except ValidationError:
        return (False if refunded else supports(order)), False


def _entry_enabled(order_id):
    if enabled():
        return True
    from .ordinary_transfer_capability import enabled as ordinary_transfer_enabled
    from .models import OrderTransferNegotiationEvidence
    if ordinary_transfer_enabled() and OrderTransferNegotiationEvidence.objects.filter(order_id=order_id).exists():
        return True
    from .principal_segments import enabled_for_order
    return enabled_for_order(Order.objects.filter(pk=order_id).first())


def enter_points_report_scope(scopes, order, *, include_untransferred=False):
    if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True:
        return order
    from .points_service_payment import needs_integrated_processing
    from .models import OrderOriginalShareTransfer
    if not needs_integrated_processing(order):
        return order
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        from .points_transfer_refund_access import after_transfer
        return scopes.enter_context(after_transfer(order.pk, phase='report'))
    if include_untransferred:
        from .points_business_access import service_order
        has_paid_addons = order.addon_intents.filter(payment__isnull=False).exists()
        return scopes.enter_context(service_order(order.pk, phase='report', addon=has_paid_addons))
    return order


def _latest(report):
    return report.submissions.order_by('-version').first() if report else None


def _person_freeze_until(segment):
    """All of this person's required segments share their last valid submission."""
    order = segment.attribution.order
    if order.service_clock_version is None or order.service_freeze_hours is None:
        return None
    segments = OrderParticipationSegment.objects.filter(attribution=segment.attribution,
        provider_account_id=segment.provider_account_id, started_at__isnull=False).filter(
            Q(released_at__isnull=True) | Q(original_share__gt=0))
    timestamps = []
    for part in segments:
        report = OrderSegmentReport.objects.filter(segment=part).first()
        latest = _latest(report)
        if (not report or report.status not in ('PENDING', 'APPROVED') or not latest
                or latest.version != report.version or latest.status != report.status):
            return None
        timestamps.append(latest.submitted_at)
    return max(timestamps) + timedelta(hours=order.service_freeze_hours) if timestamps else None


def report_data(segment, request, *, staff=False):
    """Return saved submission versions without assigning payout amounts."""
    from common.private_media import private_media_url
    report = OrderSegmentReport.objects.filter(segment=segment).first()
    if not report:
        return None
    if staff and report.status == 'DRAFT' and not report.version:
        return None
    versions = list(report.submissions.order_by('-version'))
    history = []
    for item in versions:
        history.append({'version':item.version, 'status':item.status, 'submitted_at':item.submitted_at,
            'reviewed_at':item.reviewed_at, 'review_remark':item.review_remark,
            'entry_image_url':private_media_url(request, item.entry_image),
            'completion_image_url':private_media_url(request, item.completion_image),
            'result_images':[{'id':image.pk, 'url':private_media_url(request, image.image)}
                             for image in item.result_images.order_by('pk')]})
    submission = dict(history[0]) if history else None
    if submission is not None:
        submission['freeze_until'] = _person_freeze_until(segment) if submission['status'] in ('PENDING','APPROVED') else None
    draft_images = [] if staff else [{'id':image.pk, 'url':private_media_url(request, image.image)}
                                      for image in report.draft_images.filter(active=True).order_by('pk')]
    return {'status':report.status, 'version':report.version, 'submission':submission, 'history':history,
            'draft_images':draft_images}


def _fingerprint(request, order_id):
    digest = hashlib.sha256()
    image = request.FILES.get('image')
    if image:
        for chunk in image.chunks():
            digest.update(chunk)
        image.seek(0)
    payload = {'order':order_id, 'segment':request.data.get('segment_id'), 'action':request.data.get('action'),
               'version':request.data.get('version'), 'image_id':request.data.get('image_id'), 'image':digest.hexdigest()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class SegmentReportActionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @provider_report_operation
    @order_receipt('order.segment-report', 'report', _fingerprint)
    def post(self, request, order_id):
        if not _entry_enabled(order_id):
            return Response({'code':404, 'msg':'当前不支持逐段报单'})
        if request.account.account_type != 'PROVIDER':
            return Response({'code':403, 'msg':'仅本人可操作履约报单'}, status=403)
        if not request.data.get('client_request_id'):
            return Response({'code':400, 'msg':'请使用请求编号提交'})
        try:
            segment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('segment_id'))
        except serializers.ValidationError:
            return Response({'code':400, 'msg':'请选择本人履约记录'})
        action = request.data.get('action')
        if action not in ('open', 'upload', 'delete', 'submit', 'withdraw'):
            return Response({'code':400, 'msg':'报单操作无效'})
        from contextlib import ExitStack
        with transaction.atomic(), ExitStack() as scopes:
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code':409, 'msg':'订单当前不支持逐段报单'}, status=409)
            order = enter_points_report_scope(scopes, order)
            if not supports(order):
                return Response({'code':409, 'msg':'订单当前不支持逐段报单'}, status=409)
            if order.settled_at:
                return Response({'code':409, 'msg':'订单已结算，报单不可再修改'}, status=409)
            segment = OrderParticipationSegment.objects.select_for_update().filter(pk=segment_id,
                attribution__order=order, attribution__status='ACTIVE', provider_account=request.account,
                started_at__isnull=False).first()
            if not segment or (segment.released_at and not segment.original_share):
                return Response({'code':404, 'msg':'本人可报单的履约记录不存在'}, status=404)
            if (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                    and segment.provider_business_cycle != request.account.business_cycle):
                return Response({'code':409, 'msg':'履约记录来源周期待核对'}, status=409)
            report = OrderSegmentReport.objects.select_for_update().filter(segment=segment).first()
            if action == 'open' and not report:
                report = OrderSegmentReport.objects.create(segment=segment)
            if not report:
                return Response({'code':400, 'msg':'请先创建本次履约报单'})
            latest = _latest(report)
            if ((report.version == 0 and (latest is not None or report.status != 'DRAFT'))
                    or (report.version > 0 and (not latest or latest.version != report.version
                        or (report.status == 'DRAFT' and latest.status != 'WITHDRAWN')
                        or (report.status != 'DRAFT' and latest.status != report.status)))):
                return Response({'code':409, 'msg':'报单版本记录不完整，请联系客服核对'}, status=409)
            if action in ('upload', 'delete', 'submit') and report.status not in ('DRAFT','REJECTED'):
                return Response({'code':409, 'msg':'当前报单不可修改，请刷新核对'}, status=409)
            if action == 'upload':
                try:
                    image = serializers.ImageField().run_validation(request.FILES.get('image'))
                except (serializers.ValidationError, DjangoValidationError):
                    return Response({'code':400, 'msg':'请上传有效战绩截图'})
                if report.draft_images.filter(active=True).count() >= 9:
                    return Response({'code':400, 'msg':'战绩截图最多上传 9 张'})
                OrderSegmentReportImage.objects.create(report=report, image=image)
            elif action == 'delete':
                try:
                    image_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('image_id'))
                except serializers.ValidationError:
                    return Response({'code':400, 'msg':'请选择战绩截图'})
                image = report.draft_images.filter(pk=image_id, active=True).first()
                if not image:
                    return Response({'code':404, 'msg':'截图不存在'}, status=404)
                image.active = False; image.save(update_fields=['active'])
            elif action == 'submit':
                try:
                    version = serializers.IntegerField(min_value=0).run_validation(request.data.get('version'))
                except serializers.ValidationError:
                    return Response({'code':400, 'msg':'报单版本无效'})
                if version != report.version:
                    return Response({'code':409, 'msg':'报单已更新，请刷新核对'}, status=409)
                if not segment.released_at and order.status != Order.Status.COMPLETED:
                    return Response({'code':400, 'msg':'本次履约结束后才能提交报单'})
                images = list(report.draft_images.filter(active=True).order_by('pk'))
                if not segment.entry_image or not segment.completion_image or not images:
                    return Response({'code':400, 'msg':'请补齐本次入队、结单及战绩截图'})
                submission = OrderSegmentReportSubmission.objects.create(report=report, version=report.version + 1,
                    status='PENDING', entry_image=segment.entry_image.name, completion_image=segment.completion_image.name,
                    submitted_at=timezone.now())
                OrderSegmentSubmissionImage.objects.bulk_create([OrderSegmentSubmissionImage(submission=submission,
                    image=image.image.name) for image in images])
                report.version = submission.version; report.status = 'PENDING'
                report.save(update_fields=['version', 'status', 'updated_at'])
            elif action == 'withdraw':
                try:
                    version = serializers.IntegerField(min_value=1).run_validation(request.data.get('version'))
                except serializers.ValidationError:
                    return Response({'code':400, 'msg':'报单版本无效'})
                latest = _latest(report)
                if report.status != 'PENDING' or not latest or latest.version != version or latest.status != 'PENDING':
                    return Response({'code':409, 'msg':'报单状态已变化，请刷新核对'}, status=409)
                latest.status = 'WITHDRAWN'; latest.save(update_fields=['status'])
                report.status = 'DRAFT'; report.save(update_fields=['status', 'updated_at'])
            from .notifier import notify_order_update
            transaction.on_commit(lambda: notify_order_update(order))
        from .serializers import OrderSerializer
        return Response({'code':0, 'msg':'履约报单已更新', 'data':OrderSerializer(order, context={'request':request}).data})


class SegmentReportReviewView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, order_id, segment_id):
        if not _entry_enabled(order_id):
            return Response({'code':404, 'msg':'当前不支持逐段报单'})
        if not is_console_account(request.account) or 'report:audit' not in get_account_permissions(request.account):
            return Response({'code':403, 'msg':'无报单审核权限'}, status=403)
        action, remark = request.data.get('action'), request.data.get('remark', '')
        if action not in ('approve','reject') or not isinstance(remark, str) or len(remark) > 500 or (action == 'reject' and len(remark.strip()) < 2):
            return Response({'code':400, 'msg':'请选择审核结果；驳回时须填写原因'})
        try:
            version = serializers.IntegerField(min_value=1).run_validation(request.data.get('version'))
        except serializers.ValidationError:
            return Response({'code':400, 'msg':'报单版本无效'})
        lifecycle_provider = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.models import ClubAccount
            owner_id = OrderParticipationSegment.objects.filter(pk=segment_id,
                attribution__order_id=order_id).values_list('provider_account_id', flat=True).first()
            lifecycle_provider = ClubAccount.objects.filter(pk=owner_id,
                account_type=ClubAccount.AccountType.PROVIDER).first()
            if lifecycle_provider is None:
                return Response({'code':409, 'msg':'报单陪玩归属待核对'}, status=409)
            from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
            try:
                ensure_provider_lifecycle(lifecycle_provider)
            except ProviderLifecycleError:
                return Response({'code':503, 'msg':'报单陪玩账号状态暂不可核验'}, status=503)
        from contextlib import ExitStack
        with transaction.atomic(), ExitStack() as scopes:
            if lifecycle_provider:
                from club_accounts.provider_lifecycle import lock_provider_operation
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    return Response({'code':503, 'msg':'报单陪玩账号状态暂不可核验'}, status=503)
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code':409, 'msg':'订单当前不支持逐段审核'}, status=409)
            order = enter_points_report_scope(scopes, order)
            if not supports(order):
                return Response({'code':409, 'msg':'订单当前不支持逐段审核'}, status=409)
            segment = OrderParticipationSegment.objects.select_for_update().filter(pk=segment_id, attribution__order=order).first()
            if lifecycle_provider and (not segment or segment.provider_account_id != lifecycle_provider.pk
                    or segment.provider_business_cycle != lifecycle_provider.business_cycle
                    or not ClubAccount.objects.filter(pk=lifecycle_provider.pk,
                        account_type=ClubAccount.AccountType.PROVIDER,
                        business_cycle=lifecycle_provider.business_cycle).exists()):
                return Response({'code':409, 'msg':'报单陪玩归属或账号周期已变化，请重新核对'}, status=409)
            report = OrderSegmentReport.objects.select_for_update().filter(segment=segment).first() if segment else None
            latest = _latest(report)
            if (not report or report.status != 'PENDING' or report.version != version
                    or not latest or latest.version != version or latest.status != 'PENDING'):
                return Response({'code':409, 'msg':'报单状态已变化，请刷新核对'}, status=409)
            latest.status = 'APPROVED' if action == 'approve' else 'REJECTED'
            latest.reviewed_at = timezone.now(); latest.reviewer = request.account; latest.review_remark = remark.strip()
            latest.save(update_fields=['status','reviewed_at','reviewer','review_remark'])
            report.status = latest.status; report.save(update_fields=['status','updated_at'])
            from .notifier import notify_order_update
            transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code':0, 'msg':'凭证审核已记录，收益仍待结算', 'data':{'status':report.status, 'version':report.version}})
