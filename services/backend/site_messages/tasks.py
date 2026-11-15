"""Retry creation of pending store-local operator messages."""
from celery import shared_task
from django.utils import timezone


@shared_task
def recover_order_assignment_notices():
    from .order_assignment_notice import enabled, deliver
    from .report_approval_notice import enabled as report_enabled, deliver as deliver_report
    if report_enabled():
        from .models import ReportApprovalNotice
        import logging
        for notice_id in ReportApprovalNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True).iterator(chunk_size=200):
            try:
                deliver_report(notice_id)
            except Exception:
                logging.getLogger(__name__).exception('报单审核通知待恢复 intent=%s', notice_id)
    if not enabled():
        return 0
    from .models import OrderAssignmentNotice
    import logging
    ids = OrderAssignmentNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for pk in ids.iterator(chunk_size=200):
        try:
            deliver(pk)
        except Exception:
            logging.getLogger(__name__).exception('指定订单通知待恢复 intent=%s', pk)
        count += 1
    return count


@shared_task
def drain_manual_messages():
    from .manual_push import deliver_one
    from .models import ManualMessageRecipient
    ids = list(ManualMessageRecipient.objects.filter(status=ManualMessageRecipient.Status.PENDING,
        next_attempt_at__lte=timezone.now()).order_by('next_attempt_at', 'pk').values_list('pk', flat=True)[:500])
    for recipient_id in ids:
        deliver_one(recipient_id)
    return len(ids)


@shared_task
def recover_checkin_notices():
    from django.conf import settings
    if not getattr(settings, 'CHECKIN_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from users.models import CheckinMonthProgress
    from .checkin_notice import deliver
    ids = (CheckinMonthProgress.objects.filter(full_attendance_notice_intent=True,
        notice_delivery__isnull=True).order_by('pk').values_list('pk', flat=True))
    count = 0
    for progress_id in ids.iterator(chunk_size=200):
        deliver(progress_id)
        count += 1
    return count


@shared_task
def recover_reservation_invites():
    from django.conf import settings
    if not getattr(settings, 'RESERVATION_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from orders.models import Reservation, ReservationSeatEvent
    from .reservation_invite import deliver
    singles = Reservation.objects.filter(invitation_notice_intent__isnull=False,
        invitation_notice_delivery__isnull=True).order_by('pk').values_list('pk', flat=True)
    seats = ReservationSeatEvent.objects.filter(invitation_notice_intent__isnull=False,
        invitation_notice_delivery__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for kind, rows in (('single', singles), ('seat', seats)):
        for pk in rows.iterator(chunk_size=200):
            deliver(kind, pk)
            count += 1
    return count


@shared_task
def recover_reservation_status_notices():
    from django.conf import settings
    decisions = getattr(settings, 'RESERVATION_STATUS_NOTICE_RECOVERY_ENABLED', False)
    expiry = getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
    if not decisions and not expiry:
        return 0
    from orders.models import ReservationSeatEvent, ReservationStatusLog
    from .reservation_status import deliver
    events = []
    if decisions:
        events.append(('status', ReservationStatusLog.objects.filter(action__in=('CONFIRM', 'REJECT'))))
    seat_actions = (['CONFIRMED', 'REJECTED'] if decisions else []) + (['TIMEOUT'] if expiry else [])
    events.append(('seat', ReservationSeatEvent.objects.filter(action__in=seat_actions)))
    count = 0
    for kind, queryset in events:
        ids = queryset.filter(status_notice_intent__isnull=False,
            status_notice_delivery__isnull=True).order_by('pk').values_list('pk', flat=True)
        for pk in ids.iterator(chunk_size=200):
            deliver(kind, pk)
            count += 1
    return count


@shared_task
def recover_reservation_event_notices():
    from django.conf import settings
    actions = []
    if getattr(settings, 'RESERVATION_CANCELLATION_NOTICE_RECOVERY_ENABLED', False):
        actions.append('CANCEL')
    if getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False):
        actions.append('EXPIRE')
    if getattr(settings, 'RESERVATION_CONVERSION_NOTICE_RECOVERY_ENABLED', False):
        actions.append('CONVERT')
    if (getattr(settings, 'RESERVATION_TEAM_CONFIRMED_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        actions.append('CONFIRM')
    if not actions:
        return 0
    from orders.models import ReservationStatusLog
    from .reservation_event import deliver
    ids = ReservationStatusLog.objects.filter(action__in=actions, notification_intents__isnull=False,
        event_notice_batch__completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for pk in ids.iterator(chunk_size=200):
        deliver(pk)
        count += 1
    return count


@shared_task
def recover_audition_notices():
    from django.conf import settings
    if not getattr(settings, 'AUDITION_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from audition.models import AuditionSignup
    from .audition_notice import deliver
    ids = AuditionSignup.objects.filter(audit_notice_intent__isnull=False,
        notice_delivery__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for pk in ids.iterator(chunk_size=200):
        deliver(pk)
        count += 1
    return count


@shared_task
def recover_grab_result_notices():
    from django.conf import settings
    if not getattr(settings, 'GRAB_RESULT_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from orders.models import OrderGrabSingleRoundState
    from .grab_result_notice import deliver
    ids = OrderGrabSingleRoundState.objects.filter(notification_intents__isnull=False,
        notice_batch__completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for state_id in ids.iterator(chunk_size=200):
        deliver(state_id)
        count += 1
    return count


@shared_task
def recover_grab_team_result_notices():
    from django.conf import settings
    if not getattr(settings, 'GRAB_RESULT_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from orders.models import OrderGrabTeamRoundState
    from .grab_team_result_notice import deliver
    ids = OrderGrabTeamRoundState.objects.filter(notification_intents__isnull=False,
        notice_batch__completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for state_id in ids.iterator(chunk_size=200):
        deliver(state_id)
        count += 1
    return count


@shared_task
def recover_grab_disqualification_notices():
    from django.conf import settings
    if not getattr(settings, 'GRAB_RESULT_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from orders.models import OrderGrabSingleAdmission, OrderGrabTeamDraft
    from .grab_disqualification_notice import deliver
    count = 0
    for kind, model in (('single', OrderGrabSingleAdmission), ('team', OrderGrabTeamDraft)):
        ids = model.objects.filter(disqualification_notice_intents__isnull=False,
            disqualification_notice_batch__completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
        for source_id in ids.iterator(chunk_size=200):
            deliver(kind, source_id)
            count += 1
    return count


@shared_task
def recover_grab_invites():
    from django.conf import settings
    if not getattr(settings, 'GRAB_INVITE_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from django.db.models import Exists, OuterRef
    from orders.models import OrderGrabReceiptProbe
    from .models import GrabInviteNoticeDelivery
    from .grab_invite import deliver
    pending = GrabInviteNoticeDelivery.objects.filter(receipt_id=OuterRef('pk'))
    ids = (OrderGrabReceiptProbe.objects.filter(kind='INVITE', state='BEFORE', actor_cycle_guarded=True,
            invitee_business_cycle__gt=0, pass_evidence__state='KNOWN')
           .annotate(notice_exists=Exists(pending)).filter(notice_exists=False).order_by('pk')
           .values_list('pk', flat=True))
    count = 0
    for receipt_id in ids.iterator(chunk_size=200):
        try:
            deliver(receipt_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('抢单邀请通知待恢复 receipt=%s', receipt_id)
        count += 1
    return count


@shared_task
def recover_order_application_notices():
    from django.conf import settings
    if not getattr(settings, 'ORDER_APPLICATION_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import OrderApplicationNotice
    from .order_application_notice import deliver
    ids = OrderApplicationNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('订单报名通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_aftersale_closure_notices():
    from django.conf import settings
    if not getattr(settings, 'AFTERSALE_CLOSURE_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import AfterSaleClosureNotice
    from .aftersale_closure_notice import deliver
    ids = AfterSaleClosureNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('售后结案通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_aftersale_open_notices():
    from django.conf import settings
    if not getattr(settings, 'AFTERSALE_OPEN_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import AfterSaleOpenNotice
    from .aftersale_open_notice import deliver
    ids = AfterSaleOpenNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('售后登记通知待恢复 intent=%s', intent_id)
        count += 1
    from .models import LowRatingNotice
    from .low_rating_notice import deliver as deliver_rating
    ids = LowRatingNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver_rating(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('低星评价通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_evaluation_notices():
    from django.conf import settings
    normal_enabled = getattr(settings, 'EVALUATION_NOTICE_RECOVERY_ENABLED', False)
    replies_enabled = getattr(settings, 'EVALUATION_REPLY_NOTICE_RECOVERY_ENABLED', False)
    if not normal_enabled and not replies_enabled:
        return 0
    count = 0
    from .models import EvaluationNotice, EvaluationReplyNotice
    from .evaluation_notice import deliver as deliver_evaluation
    from .evaluation_reply_notice import deliver as deliver_reply
    for enabled, model, deliver in ((normal_enabled, EvaluationNotice, deliver_evaluation),
                                    (replies_enabled, EvaluationReplyNotice, deliver_reply)):
        if not enabled:
            continue
        ids = model.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
        for intent_id in ids.iterator(chunk_size=200):
            try:
                deliver(intent_id)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('服务评价通知待恢复 model=%s intent=%s', model.__name__, intent_id)
            count += 1
    return count


@shared_task
def recover_room_confirmation_notices():
    from django.conf import settings
    if not getattr(settings, 'ROOM_CONFIRMATION_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import RoomConfirmationNotice
    from .room_confirmation_notice import deliver
    ids = RoomConfirmationNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('房间结单通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_room_review_notices():
    from django.conf import settings
    if not getattr(settings, 'ROOM_REVIEW_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import RoomReviewNotice
    from .room_review_notice import deliver
    ids = RoomReviewNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('房间核实通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_room_objection_notices():
    from django.conf import settings
    if not getattr(settings, 'ROOM_OBJECTION_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import RoomObjectionNotice
    from .room_objection_notice import deliver
    ids = RoomObjectionNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('房间异议通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_order_completion_notices():
    from django.conf import settings
    if not getattr(settings, 'ORDER_COMPLETION_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import OrderCompletionNotice
    from .order_completion_notice import deliver
    ids = OrderCompletionNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('订单完成通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_order_timeout_notices():
    from django.conf import settings
    if not getattr(settings, 'ORDER_TIMEOUT_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import OrderTimeoutNotice
    from .order_timeout_notice import deliver
    ids = OrderTimeoutNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('订单超时通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_customer_cancellation_notices():
    from django.conf import settings
    if not getattr(settings, 'CUSTOMER_CANCEL_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import CustomerCancellationNotice
    from .order_customer_cancel_notice import deliver
    ids = CustomerCancellationNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('客户取消通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_order_refund_notices():
    from django.conf import settings
    if not getattr(settings, 'ORDER_REFUND_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import OrderRefundNotice
    from .order_refund_notice import deliver
    ids = OrderRefundNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('订单退款通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_points_aftersale_refund_notices():
    from django.conf import settings
    if not getattr(settings, 'POINTS_AFTERSALE_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import PointsAftersaleRefundNotice
    from .points_aftersale_refund_notice import deliver
    ids = PointsAftersaleRefundNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('积分售后退款通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_addon_refund_notices():
    from django.conf import settings
    if not getattr(settings, 'ADDON_REFUND_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import AddonRefundNotice
    from .addon_refund_notice import deliver
    ids = AddonRefundNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('加购退款通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_zero_refund_closure_notices():
    from django.conf import settings
    if not getattr(settings, 'ZERO_REFUND_CLOSURE_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import ZeroRefundClosureNotice
    from .zero_refund_closure_notice import deliver
    ids = ZeroRefundClosureNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('零退款结案通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_addon_handover_result_notices():
    from django.conf import settings
    if not getattr(settings, 'ADDON_HANDOVER_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import AddonHandoverResultNotice
    from .addon_handover_notice import deliver
    ids = AddonHandoverResultNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('加购接替结果通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_service_transfer_notices():
    from django.conf import settings
    if not getattr(settings, 'SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import ServiceTransferNotice
    from .service_transfer_notice import deliver
    ids = ServiceTransferNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('服务转单确认通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_reservation_seat_timeout_notices():
    from django.conf import settings
    if not getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False):
        return 0
    from .models import ReservationSeatTimeoutNotice
    from .reservation_seat_timeout_notice import deliver
    ids = ReservationSeatTimeoutNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('预约席位超时通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_prestart_handoff_notices():
    from .prestart_handoff_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import PrestartHandoffNotice
    ids = PrestartHandoffNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('服务前改派通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_reservation_team_declined_notices():
    from .reservation_team_declined import enabled, deliver
    if not enabled():
        return 0
    from .models import ReservationTeamDeclinedNotice
    ids = ReservationTeamDeclinedNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('双陪拒绝通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_reservation_team_replaced_notices():
    from .reservation_team_replaced import enabled, deliver
    if not enabled():
        return 0
    from .models import ReservationTeamReplacedNotice
    ids = ReservationTeamReplacedNotice.objects.filter(completed_at__isnull=True).order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('双陪重选通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_negotiation_proposal_notices():
    from .negotiation_proposal_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import NegotiationProposalNotice
    ids = NegotiationProposalNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('协商方案通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_gift_receipt_notices():
    from .gift_receipt_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import GiftReceiptNotice
    ids = GiftReceiptNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('赠礼通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_staff_start_notices():
    from .staff_start_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import StaffStartNotice
    ids = StaffStartNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for intent_id in ids.iterator(chunk_size=200):
        try:
            deliver(intent_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('客服开工通知待恢复 intent=%s', intent_id)
        count += 1
    return count


@shared_task
def recover_addon_payment_notices():
    from .addon_payment_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import AddonPaymentNotice
    ids = AddonPaymentNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('加购付款站内消息待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_addon_completion_notices():
    from .addon_completion_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import AddonCompletionNotice
    ids = AddonCompletionNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('加购完成确认站内消息待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_addon_refund_proposal_notices():
    from .addon_refund_proposal_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import AddonRefundProposalNotice
    ids = AddonRefundProposalNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('加购退款方案站内消息待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_addon_handover_proposal_notices():
    from .addon_handover_proposal_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import AddonHandoverProposalNotice
    ids = AddonHandoverProposalNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('加购接替方案站内消息待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_deposit_event_notices():
    from .deposit_event_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import DepositEventNotice
    ids = DepositEventNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('押金站内消息待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_deposit_application_notices():
    from .deposit_application_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import DepositApplicationNotice
    ids = DepositApplicationNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('退押金申请通知待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_recharge_receipt_notices():
    from .recharge_receipt_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import RechargeReceiptNotice
    ids = RechargeReceiptNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('充值站内消息待恢复 notice=%s', notice_id)
        count += 1
    return count


@shared_task
def recover_recharge_gift_decision_notices():
    from .recharge_gift_decision_notice import enabled, deliver
    if not enabled():
        return 0
    from .models import RechargeGiftDecisionNotice
    ids = RechargeGiftDecisionNotice.objects.filter(status='PENDING').order_by('pk').values_list('pk', flat=True)
    count = 0
    for notice_id in ids.iterator(chunk_size=200):
        try:
            deliver(notice_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('充值赠送审批通知待恢复 notice=%s', notice_id)
        count += 1
    return count
