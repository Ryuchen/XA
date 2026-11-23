from .points_business_access import scoped_refund_case_action, scoped_transfer_open_action, scoped_customer_aftersale_action
from .referral_refund_lifecycle import protect_execution as protect_referral_execution
from .gift_refund_lifecycle import protect_gift_refund
from .aftersale_open_lifecycle import protect_open
from stores.db import transaction
import logging
from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.viewsets import ReadOnlyModelViewSet
from club_accounts.permissions import IsClubAccountAuthenticated
from wallet.services import WalletAddressingError
from console.mixins import EnvelopeViewSetMixin
from .models import Order, AfterSale
from .notifier import notify_order_update
from engagement.confirmation import aftersale_deadline
from .refunds import can_request_aftersale, RefundError
from .aftersale_scope import applies_to, normalize_scope, validate_scope


def append_history(row):
    entry = {'status':row.status,'reason':row.reason,'resolution':row.resolution,
        'created_at':row.created_at.isoformat(), 'resolved_at':row.resolved_at.isoformat() if row.resolved_at else None,
        'operator_id':row.operator_id,'client_request_id':row.client_request_id,
        'affected_provider_account_ids':row.affected_provider_account_ids,
        'affected_segment_ids':row.affected_segment_ids}
    if row.addon_payment_id is not None:
        entry['addon_payment_id'] = row.addon_payment_id
    row.history = [*row.history, entry]


def notify_aftersale_reports(order):
    """Refresh report-only reviewers when a case changes the settlement context."""
    try:
        from wallet.models import ProviderReport
        from .notifier import notify_report_update
        for report in ProviderReport.objects.filter(order=order):
            notify_report_update(report, notify_owner=False)
    except Exception:
        logging.getLogger(__name__).warning('售后报单通知失败: order_id=%s', order.pk, exc_info=True)


def schedule_aftersale_notices(order, case, *, resolved, notify_customer=False, source_case=None):
    """Notify offline parties without exposing the customer's private case text."""
    from .services import push_message_safe
    from site_messages.aftersale_open_notice import enabled as open_enabled, record as record_open
    if resolved and open_enabled() and isinstance(case, AfterSale):
        from site_messages.models import AfterSaleOpenNotice, Message
        prior = AfterSaleOpenNotice.objects.filter(case=case, case_opened_at=case.created_at,
                                                    message__isnull=False).values_list('message_id', flat=True)
        Message.objects.filter(pk__in=prior).update(title='售后已处理', preview='售后已处理，请查看订单中的处理结果。')

    recipients = []
    if resolved:
        recipients.append((order.customer_id, order.customer_account_id, '售后已处理',
                           '售后已处理，请查看订单中的处理结果。'))
    elif notify_customer:
        recipients.append((order.customer_id, order.customer_account_id, '售后已登记',
                           '客服已登记售后，请查看订单中的处理进度。'))
    meaningful = Q(released_at__isnull=True) | Q(settlement_base__gt=0) | Q(provider_income__gt=0)
    from django.conf import settings
    if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        meaningful |= Q(platform_subsidy_share__amount__gt=0)
    shares = list(order.providers.filter(meaningful).values_list('provider_id', 'provider_account_id'))
    if not shares and order.provider_id:
        shares = [(order.provider_id, order.provider_account_id)]
    shares = [(user_id, account_id) for user_id, account_id in shares if applies_to(case, account_id)]
    if resolved:
        title, preview = '订单售后已处理', '订单售后已处理，请查看订单；收益以结算状态及钱包流水为准。'
    else:
        title = '订单售后处理中'
        preview = ('订单售后处理中，请查看订单；已结算收益以钱包流水为准。' if order.settled_at else
                   '订单售后处理中，未结算收益暂缓；已结算收益请查看钱包流水。')
    recipients.extend((user_id, account_id, title, preview) for user_id, account_id in shares)
    from site_messages.zero_refund_closure_notice import enabled as zero_notice_enabled, prepare as prepare_zero_notice
    zero_source = (prepare_zero_notice(order, case) if resolved and zero_notice_enabled()
                   and isinstance(case, AfterSale) else None)
    if zero_source:
        from site_messages.zero_refund_closure_notice import record_all as record_zero_notices
        record_zero_notices(order, case, zero_source)
        return
    from site_messages.points_aftersale_refund_notice import enabled as points_notice_enabled, prepare as prepare_points_notice
    points_source = (prepare_points_notice(order, case) if resolved and points_notice_enabled()
                     and isinstance(case, AfterSale) else None)
    seen_users, seen_accounts = set(), set()
    for user_id, account_id, title, preview in recipients:
        if (not user_id and not account_id) or (user_id and user_id in seen_users) or (account_id and account_id in seen_accounts):
            continue
        if user_id:
            seen_users.add(user_id)
        if account_id:
            seen_accounts.add(account_id)
        from site_messages.order_cycle import recipient_cycle
        cycle = recipient_cycle(order, account_id)
        from site_messages.aftersale_closure_notice import enabled, record
        if points_source and account_id == order.customer_account_id:
            from site_messages.points_aftersale_refund_notice import record as record_points_notice
            record_points_notice(points_source, order=order, cycle=cycle, legacy_user_id=user_id)
        elif resolved and enabled() and account_id:
            record(case, account_id=account_id, legacy_user_id=user_id, cycle=cycle,
                   account_type='BOSS' if account_id == order.customer_account_id else 'PROVIDER',
                   title=title, preview=preview)
        elif not resolved and open_enabled() and account_id and isinstance(source_case or case, AfterSale):
            record_open(source_case or case, account_id=account_id, legacy_user_id=user_id, cycle=cycle,
                        account_type='BOSS' if account_id == order.customer_account_id else 'PROVIDER',
                        title=title, preview=preview)
        else:
            transaction.on_commit(lambda user_id=user_id, account_id=account_id, title=title, preview=preview, cycle=cycle:
                push_message_safe(recipient_id=user_id, recipient_account_id=account_id, title=title,
                                  preview=preview, msg_type='ORDER', related_order_id=order.pk,
                                  recipient_business_cycle=cycle, require_cycle=True))


class AfterSaleSerializer(EntityNumberModelSerializer):
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    affected_provider_names = serializers.SerializerMethodField()
    completed_followup_supported = serializers.SerializerMethodField()

    def to_representation(self, instance):
        from copy import deepcopy
        data = super().to_representation(instance)
        history = data.get('history')
        if not isinstance(history, list):
            return data
        history = deepcopy(history)
        data['history'] = history
        from chat.authorization import can_read_session
        from chat.models import ChatSession
        account = getattr(self.context.get('request'), 'account', None)
        session_ids = {entry.get('consultation_session_id') for entry in history
                       if isinstance(entry, dict) and entry.get('consultation_session_id')}
        sessions = {row.pk: row for row in ChatSession.objects.filter(pk__in=session_ids)}
        fields = ('consultation_message_id', 'consultation_session_id', 'consultation_account_id',
                  'consultation_business_cycle')
        for entry in history:
            if isinstance(entry, dict) and entry.get('consultation_session_id'):
                session = sessions.get(entry['consultation_session_id'])
                if not session or not account or account.account_type != 'STAFF' or not can_read_session(account, session):
                    for field in fields:
                        entry.pop(field, None)
        return data

    def get_completed_followup_supported(self, obj):
        from .ordinary_addon_followup_preview import applies as addon_followup
        if obj.status == 'OPEN' and obj.order.status == Order.Status.COMPLETED and addon_followup(obj.order):
            from .ordinary_addon_followup_termination import endpoint_available
            return endpoint_available(obj)
        from .negotiation_followup import completed_followup_execution_enabled
        return bool(obj.status == 'OPEN' and obj.order.status == Order.Status.COMPLETED
                    and completed_followup_execution_enabled())

    def get_affected_provider_names(self, obj):
        ids = obj.affected_provider_account_ids
        if (not isinstance(ids, list) or not ids
                or any(type(account_id) is not int or account_id <= 0 for account_id in ids)):
            return []
        names = {row.provider_account_id: row.provider_name_snapshot or '陪玩'
                 for row in obj.order.providers.filter(provider_account_id__in=ids)}
        if obj.order.provider_account_id in ids:
            names.setdefault(obj.order.provider_account_id, obj.order.provider_name_snapshot or '陪玩')
        return [names.get(account_id, '陪玩信息待核对') for account_id in ids]

    class Meta:
        model = AfterSale
        fields = '__all__'


class AfterSaleView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def customer_orders(self, request):
        ownership = Q(customer_account=request.account)
        if request.legacy_user is not None:
            ownership |= Q(customer_account__isnull=True, customer=request.legacy_user)
        return Order.objects.filter(ownership)

    @transaction.atomic
    @scoped_customer_aftersale_action
    def get(self, request, order_id):
        order = get_object_or_404(self.customer_orders(request).select_for_update(), pk=order_id)
        row = order.aftersales.first()
        if not row:
            return Response({'code': 0, 'data': None})
        data = AfterSaleSerializer(row).data
        if order.status == Order.Status.COMPLETED:
            from .aftersale_segment_scope import closed_segments_locked
            closed_segments = closed_segments_locked(order)
            if closed_segments is not None:
                from .models import OrderParticipationSegment
                segments = list(OrderParticipationSegment.objects.filter(
                    attribution__order=order, started_at__isnull=False).values_list('pk', 'provider_account_id'))
                accounts = {account for _, account in segments}
                data['confirmed_termination_segment_ids'] = sorted(closed_segments)
                data['confirmed_termination_provider_ids'] = sorted(account for account in accounts if all(
                    pk in closed_segments for pk, owner in segments if owner == account))
            else:
                from .negotiation_aftersale_scope import closed_provider_accounts
                data['confirmed_termination_provider_ids'] = sorted(closed_provider_accounts(order))
        return Response({'code': 0, 'data': data})

    @protect_open()
    @transaction.atomic
    @scoped_customer_aftersale_action
    def post(self, request, order_id):
        order = get_object_or_404(self.customer_orders(request).select_for_update(), pk=order_id)
        reason = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('reason'))
        request_id = serializers.CharField(max_length=64, allow_blank=True).run_validation(request.data.get('client_request_id',''))
        scope = normalize_scope(request.data.get('affected_provider_account_ids'))
        requested_segments = normalize_scope(request.data.get('affected_segment_ids'))
        existing = order.aftersales.first()
        if existing:
            original = next((item for item in existing.history if request_id and item.get('client_request_id') == request_id), None)
            if not original and request_id and existing.client_request_id == request_id:
                original = {'reason': existing.reason, 'affected_provider_account_ids': existing.affected_provider_account_ids}
            if original:
                if (original.get('reason') != reason
                        or original.get('request_provider_account_ids') != scope
                        or original.get('request_segment_ids') != requested_segments):
                    return Response({'code':409, 'errorCode':'AFTERSALE_REQUEST_CONFLICT',
                                     'msg':'这次修改未提交。请查看已有申请，或联系客服补充说明。'}, status=409)
                if existing.client_request_id != request_id:
                    return Response({'code':409, 'errorCode':'AFTERSALE_REQUEST_PREVIOUS',
                                     'msg':'原售后申请已处理，当前已有更新的记录，请查看售后历史。'}, status=409)
                return Response({'code': 0, 'data': AfterSaleSerializer(existing).data, 'msg': '售后已提交'})
            if existing.status == 'OPEN':
                if (existing.reason == reason and (scope is None or existing.affected_provider_account_ids == scope)
                        and (requested_segments is None or existing.affected_segment_ids == requested_segments)):
                    if request_id and request_id != existing.client_request_id:
                        return Response({'code':409, 'errorCode':'AFTERSALE_ALREADY_OPEN',
                                         'msg':'已有售后处理中，请使用原请求编号重试或查看已有申请。'}, status=409)
                    return Response({'code': 0, 'data': AfterSaleSerializer(existing).data, 'msg': '售后已提交'})
                return Response({'code':409, 'errorCode':'AFTERSALE_ALREADY_OPEN',
                                 'msg':'已有售后处理中。本次说明未提交，请查看已有申请，或联系客服补充说明。'}, status=409)
        if not can_request_aftersale(order):
            raise serializers.ValidationError('订单已结束售后期或不符合售后条件')
        validate_scope(order, scope)
        from .aftersale_segment_scope import resolve_locked as resolve_segments
        segments = resolve_segments(order, scope, requested_segments)
        if segments is not None and scope is None:
            from .models import OrderParticipationSegment
            scope = sorted(set(OrderParticipationSegment.objects.filter(pk__in=segments,
                attribution__order=order).values_list('provider_account_id', flat=True)))
        if order.status == Order.Status.COMPLETED:
            from .negotiation_aftersale_scope import assert_scope_not_closed
            if segments is None:
                assert_scope_not_closed(order, scope)
        # A deposit refund holds provider profiles while checking open cases.
        # Serialize case creation with that check without taking order locks in
        # the opposite direction from other order operations.
        from users.models import EscortProfile
        participants = list(order.providers.values_list('provider_id', 'provider_account_id'))
        participants.append((order.provider_id, order.provider_account_id))
        users = {user_id for user_id, _ in participants if user_id}
        accounts = {account_id for _, account_id in participants if account_id}
        list(EscortProfile.objects.select_for_update().filter(
            Q(user_id__in=users) | Q(account_id__in=accounts)).order_by('pk'))
        if existing:
            row = existing
            if not row.history:
                append_history(row)
            row.status, row.reason, row.resolution = 'OPEN', reason, ''
            row.operator, row.resolved_at, row.created_at = None, None, timezone.now()
            row.client_request_id = request_id
        else:
            row = AfterSale(order=order, account=request.account, reason=reason, client_request_id=request_id)
            row.created_at = timezone.now()
        row.affected_provider_account_ids = scope
        row.affected_segment_ids = segments
        row.addon_payment_id = None
        row.save()
        append_history(row)
        # Preserve the submitted scope independently of the resolved scope:
        # omission can select the entire order or infer accounts from segments.
        row.history[-1]['request_provider_account_ids'] = normalize_scope(request.data.get('affected_provider_account_ids'))
        row.history[-1]['request_segment_ids'] = requested_segments
        row.save(update_fields=['history'])
        transaction.on_commit(lambda: notify_order_update(order))
        transaction.on_commit(lambda: notify_aftersale_reports(order))
        schedule_aftersale_notices(order, row, resolved=False)
        return Response({'code': 0, 'data': AfterSaleSerializer(row).data, 'msg': '售后已提交，等待平台处理'})


class AfterSaleViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    queryset = AfterSale.objects.select_related('order', 'account').order_by('-id')
    serializer_class = AfterSaleSerializer
    default_perm = 'order:view'
    required_perms = {'resolve':'order:settle', 'refund':'order:refund', 'open_negotiation':'support:edit',
                      'negotiation_proposal':'support:edit', 'negotiation_preview':'support:edit',
                      'negotiation_funding':'order:refund', 'negotiation_execution':'order:refund',
                      'subsidy_negotiation_execution':'order:refund'}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('order'):
            qs = qs.filter(order_id=serializers.IntegerField(min_value=1).run_validation(self.request.query_params['order']))
        if self.request.query_params.get('status'):
            qs = qs.filter(status=self.request.query_params['status'])
        return qs

    @action(detail=True, methods=['get', 'post'], url_path='negotiation-proposal')
    @scoped_refund_case_action
    def negotiation_proposal(self, request, pk=None):
        from . import negotiation_proposals as proposals
        if not proposals.enabled():
            return Response({'code':404, 'msg':'协商方案暂未开放'}, status=404)
        case = self.get_object()
        if request.method == 'GET':
            version, latest = proposals.latest_for_case(case)
            from .refunds import authorized_refund
            from .subsidy_agreement import applies as is_subsidy, enabled as subsidy_enabled
            if is_subsidy(case.order):
                from .subsidy_execution import table_available
                from .models import OrderOriginalShareTransfer
                transferred = OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=case.order).exists()
                from .subsidy_transfer_agreement import enabled as transfer_agreement_enabled
                transfer_ready = transferred and transfer_agreement_enabled()
                return Response({'code':0, 'data':{'version':version,
                    'proposal':proposals.serialize(latest, actor=request.account, staff=True) if latest else None,
                    'followup_preview_only':bool(transferred and not transfer_ready),
                    'execution_available':bool((not transferred or transfer_ready) and subsidy_enabled() and table_available()
                                               and authorized_refund(request.account))}})
            from .negotiation_zero import marked_execution_id
            from .negotiation_followup import followup_enabled, completed_followup_execution_enabled
            from .points_service_payment import needs_integrated_processing
            points_pending = needs_integrated_processing(case.order)
            from .models import OrderOriginalShareTransfer
            transferred = points_pending and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=case.order).exists()
            from .points_negotiation_preview import agreement_enabled as points_agreement_enabled
            from .points_negotiation_termination import available_for_order as points_termination_available
            followup = (case.order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED,
                                                      Order.PaymentStatus.REFUNDED)
                        or marked_execution_id(case.order) is not None)
            from .points_business_access import permits as points_permits
            formal_followup = any(points_permits(case.order, phase='refund', kind=kind) for kind in ('service', 'addon'))
            points_followup_agreement = (points_pending and not transferred and points_agreement_enabled()
                and (formal_followup or (getattr(settings, 'SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING', False)
                and getattr(settings, 'SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING', False))))
            if points_pending and transferred and followup:
                if case.order.refund_records.count() >= 3:
                    from .points_round_refund_preview import available_for_case
                elif case.order.refund_records.count() == 2:
                    from .points_third_refund_preview import available_for_case
                else:
                    from .points_transfer_followup_termination import available_for_case
                points_followup_agreement = available_for_case(case)
            if points_followup_agreement and followup and not transferred:
                from .models import OrderPointsRefundSettlementPerson
                points_followup_agreement = (formal_followup or getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING', False)
                    or OrderPointsRefundSettlementPerson.objects.filter(
                        settlement__posting__points_refund__redemption__order=case.order).exists())
            followup_only = followup and ((points_pending and not points_followup_agreement) or
                (not points_pending and not followup_enabled()) or
                (case.status == 'OPEN' and case.order.status == Order.Status.COMPLETED
                 and not points_followup_agreement and not completed_followup_execution_enabled()))
            from .ordinary_addon_followup_preview import applies as addon_followup
            if followup and not points_pending and addon_followup(case.order):
                from .ordinary_addon_followup_termination import endpoint_available as addon_endpoint_available
                followup_only = not addon_endpoint_available(case)
            points_followup_execution = False
            if points_pending and latest and getattr(getattr(latest, 'points_terms', None), 'prior_refund_id', None):
                if transferred:
                    if case.order.refund_records.count() >= 3:
                        from .points_round_refund_preview import available_for_case
                        points_followup_execution = available_for_case(case, refund_units=latest.refund_units)
                    elif case.order.refund_records.count() == 2:
                        from .points_third_refund_preview import available_for_case
                        points_followup_execution = available_for_case(case, refund_units=latest.refund_units)
                    else:
                        from .points_transfer_followup_termination import available_for_case
                        points_followup_execution = available_for_case(case)
                else:
                    from .points_followup_termination import endpoint_available
                    points_followup_execution = endpoint_available(latest.points_terms.prior_refund_id)
            return Response({'code':0, 'data':{'version':version,
                'proposal':proposals.serialize(latest, actor=request.account, staff=True) if latest else None,
                'followup_preview_only':followup_only,
                'execution_available':bool(not followup_only and authorized_refund(request.account) and
                    (points_followup_execution or (points_pending and not followup and points_termination_available(case.order))
                     or (not points_pending and
                     getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False))))}})
        data = request.data
        if not isinstance(data, dict) or set(data) != {'request_id', 'expected_version', 'refund_units', 'provider_terms', 'explanation'}:
            raise serializers.ValidationError('协商方案格式无效')
        proposal = proposals.create(case.pk, actor=request.account, **data)
        return Response({'code':0, 'data':proposals.serialize(proposal, actor=request.account, staff=True),
                         'msg':'方案已登记，等待双方确认，不会自动退款'})

    @action(detail=True, methods=['get'], url_path='negotiation-preview')
    @transaction.atomic
    @scoped_refund_case_action
    def negotiation_preview(self, request, pk=None):
        from . import negotiation_proposals as proposals
        from .negotiation_financial import preview
        if not proposals.enabled():
            return Response({'code':404, 'msg':'协商方案暂未开放'}, status=404)
        requested = serializers.IntegerField(min_value=0).run_validation(request.query_params.get('refund_units'))
        initial = self.get_object()
        order, case = proposals._lock_case(initial.pk)
        from .subsidy_agreement import applies as is_subsidy
        if is_subsidy(order):
            if requested != 0:
                raise serializers.ValidationError('免费安全箱订单退款金额必须为零')
            from .models import OrderOriginalShareTransfer
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                from .subsidy_transfer_negotiation_preview import preview_locked
                from .financial_lock import lock_order_money
                lock_order_money()
                scope = proposals._scope(order, case)
                detail = preview_locked(order, case, scope)
                from .subsidy_transfer_agreement import enabled as transfer_agreement_enabled
                from .refunds import authorized_refund
                available = transfer_agreement_enabled()
                original = detail['current_original_income_units']
                return Response({'code':0, 'data':{'refund_units':0, 'calculation_kind':'PLATFORM_SUBSIDY',
                    'segment_evidence':detail, 'provider_terms':[{'account_id':detail['account_id'],
                        'original_income_units':original, 'retained_units':original, 'burden_units':0, 'additional_units':0}],
                    'rounding_to_store_units':0, 'read_only':not available, 'agreement_available':available,
                    'execution_available':bool(available and authorized_refund(request.account))},
                    'msg':'等待双方确认后由授权客服处理' if available else '仅供核对，转单后的协商确认与执行尚未开放'})
            from .subsidy_agreement import calculate
            from .models import OrderPlatformSubsidyShare
            scope = proposals._scope(order, case)
            rows = list(OrderPlatformSubsidyShare.objects.filter(subsidy__order=order,
                assignment__provider_account_id__in=scope).select_related('assignment'))
            requested_terms = [{'account_id':row.assignment.provider_account_id, 'retained_units':row.amount} for row in rows]
            calculated = calculate(order, case, scope, 0, requested_terms)
            from .refunds import authorized_refund
            from .subsidy_execution import table_available
            return Response({'code':0, 'data':{'refund_units':0, 'provider_terms':calculated['provider_terms'],
                'calculation_kind':'PLATFORM_SUBSIDY', 'rounding_to_store_units':0,
                'agreement_available':True, 'execution_available':bool(table_available() and authorized_refund(request.account))}})
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            if requested == 0:
                from .points_zero_followup import prepare_locked
                from .refunds import authorized_refund
                _, _, _, snapshot, term = prepare_locked(order, case)
                return Response({'code': 0, 'data': {'refund_units': 0, 'refund_coin_units': 0,
                    'refund_points_hundredths': 0, 'provider_terms': [term], 'rounding_to_store_units': 0,
                    'conditional_funding': snapshot['funding'], 'read_only': False,
                    'agreement_available': True, 'execution_available': bool(authorized_refund(request.account))},
                    'msg': '等待老板与受影响陪玩确认后由授权客服结案'})
            from .models import OrderOriginalShareTransfer
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                if order.refund_records.exists():
                    if order.refund_records.count() >= 3:
                        from .points_round_refund_preview import preview_locked, available_for_case
                    elif order.refund_records.count() == 2:
                        from .points_third_refund_preview import preview_locked, available_for_case
                    else:
                        from .points_transfer_followup_preview import preview_locked
                        from .points_transfer_followup_termination import available_for_case
                    from .refunds import authorized_refund
                    calculated = preview_locked(case.pk, refund_equivalent_units=requested)
                    can_agree = (available_for_case(case, refund_units=requested)
                                 if order.refund_records.count() == 2 else available_for_case(case))
                    can_execute = can_agree and authorized_refund(request.account)
                    return Response({'code': 0, 'data': {'refund_units': requested,
                        'refund_coin_units': calculated['coin_units'],
                        'refund_points_hundredths': calculated['points_hundredths'],
                        'provider_terms': calculated['provider_terms'],
                        'rounding_to_store_units': calculated['rounding_to_store_units'],
                        'read_only': not can_agree, 'agreement_available': can_agree, 'execution_available': can_execute},
                        'msg': '等待双方确认后由授权客服处理' if can_execute else '仅供核对，转单后的追加协商退款尚未开放'})
                from .points_transfer_refund_preview import agreement_available_for_order, preview_locked
                from .points_negotiation_termination import available_for_order
                from .refunds import authorized_refund
                calculated = preview_locked(case.pk, refund_equivalent_units=requested)
                can_execute = authorized_refund(request.account) and available_for_order(order)
                can_agree = agreement_available_for_order(order)
                return Response({'code': 0, 'data': {'refund_units': requested,
                    'refund_coin_units': calculated['coin_units'], 'refund_points_hundredths': calculated['points_hundredths'],
                    'provider_terms': calculated['provider_terms'], 'rounding_to_store_units': calculated['rounding_to_store_units'],
                    'read_only': not can_agree, 'agreement_available': can_agree, 'execution_available': can_execute},
                    'msg': ('等待双方确认后由授权客服处理' if can_execute else
                            '退款尚未开放' if can_agree else '仅供核对，转单后的积分协商确认和退款尚未开放')})
            from .points_business_access import permits as points_permits
            formal_followup = any(points_permits(order, phase='refund', kind=kind) for kind in ('service', 'addon'))
            if (order.refund_records.exists()
                    and (formal_followup or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING', False))):
                from .points_followup_preview import preview_followup
                result = preview_followup(case.pk, refund_equivalent_units=requested)
                term = {key: result[key] for key in ('account_id', 'original_income_units', 'burden_units',
                                                      'retained_units')}
                term['additional_units'] = result['additional_provider_units']
                from .points_negotiation_preview import agreement_enabled
                from .models import PointsNegotiationPosting
                previous = PointsNegotiationPosting.objects.get(points_refund_id=result['prior_refund_id'])
                from .points_followup_termination import (addon_followup_available, endpoint_available,
                    formal_followup_funding_ready)
                addon_ready = addon_followup_available(order, previous, prior_paid=result['prior_paid'])
                funding_ready = not formal_followup or formal_followup_funding_ready(result)
                can_agree = (agreement_enabled() and bool(case.client_request_id)
                             and addon_ready and funding_ready
                             and (formal_followup or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING', False))
                             and (formal_followup or result['prior_paid'] or getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING', False)))
                from .refunds import authorized_refund
                can_execute = (addon_ready and funding_ready and endpoint_available(
                    result['prior_refund_id'], prior_paid=result['prior_paid']) and authorized_refund(request.account))
                from dataclasses import asdict
                return Response({'code': 0, 'data': {
                    'refund_units': requested, 'refund_coin_units': result['refund_coin_units'],
                    'refund_points_hundredths': result['refund_points_hundredths'],
                    'provider_terms': [term], 'rounding_to_store_units': result['rounding_to_store_units'],
                    'has_addons': result['has_addons'], 'conditional_funding': asdict(result['conditional_funding']),
                    'read_only': not can_agree, 'agreement_available': can_agree, 'execution_available': can_execute},
                    'msg': ('已有陪玩结算的后续退款须先完成资金核对' if not funding_ready else
                            '带加购的后续退款目前仅供核对' if result['has_addons'] and not addon_ready else
                            '原陪玩尚未结算，后续协商目前仅供核对' if not result['prior_paid'] and not can_agree else
                            ('等待双方确认后由授权客服处理' if formal_followup else '仅隔离测试可执行后续退款') if can_execute else
                            ('可确认方案，退款暂未开放' if formal_followup else '仅隔离测试确认，后续退款尚未开放') if can_agree
                            else '仅供核对，后续协商确认和退款尚未开放')})
            from .points_negotiation_preview import agreement_enabled, preview_locked
            from .points_negotiation_termination import main_refund_preview_capability
            from .refunds import authorized_refund
            calculated = preview_locked(case.pk, refund_equivalent_units=requested)
            can_agree = agreement_enabled() and bool(case.client_request_id)
            _, path_enabled = main_refund_preview_capability(order, case)
            available = can_agree and path_enabled
            return Response({'code': 0, 'data': {
                'refund_units': requested, 'refund_coin_units': calculated['coin_units'],
                'refund_points_hundredths': calculated['points_hundredths'],
                'provider_terms': calculated['provider_terms'],
                'rounding_to_store_units': calculated['rounding_to_store_units'],
                'read_only': not can_agree, 'agreement_available': can_agree,
                'execution_available': available and authorized_refund(request.account)},
                'msg': ('当事人确认后，由具备退款权限的客服核对并执行' if available else
                        '可确认方案，当前退款暂不可执行' if can_agree else '仅供核对，当前协商方案不可确认')})
        from .ordinary_transfer_followup import applies as transfer_followup_applies, calculation_locked
        if transfer_followup_applies(order):
            from .financial_lock import lock_order_money
            from .refunds import authorized_refund
            lock_order_money()
            calculated = calculation_locked(order, case, requested)
            available = bool(requested or getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False))
            sufficient = not any(item['shortfall_units'] for item in calculated['provider_funding_preview']['providers'])
            return Response({'code': 0, 'data': {'refund_units': requested,
                'provider_terms': calculated['provider_terms'],
                'segment_pending_plan': calculated['segment_pending_plan'],
                'rounding_to_store_units': calculated['rounding_to_store_units'],
                'read_only': not available, 'agreement_available': available,
                'execution_available': bool(available and sufficient and authorized_refund(request.account))},
                'msg': '陪玩承担资金不足，请先补足' if not sufficient else '确认后由授权客服处理'})
        from .negotiation_zero import marked_execution_id
        if (order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
                or marked_execution_id(order) is not None):
            from .ordinary_addon_followup_preview import applies, preview_case_locked, provider_terms
            if applies(order):
                from .settlement import SettlementError
                from .ordinary_addon_followup_termination import endpoint_enabled, zero_enabled
                from .refunds import authorized_refund
                try:
                    estimate = preview_case_locked(case.pk, refund_units=requested)
                except SettlementError as exc:
                    raise serializers.ValidationError(str(exc)) from exc
                available = bool((requested or zero_enabled()) and endpoint_enabled(order))
                return Response({'code': 0, 'data': {'refund_units': requested,
                    'provider_terms': provider_terms(estimate), 'rounding_to_store_units': 0,
                    'read_only': not available, 'agreement_available': available,
                    'execution_available': bool(available and not estimate.insufficient and authorized_refund(request.account))},
                    'msg': ('陪玩承担资金不足，补足后才能退款' if available and estimate.insufficient else
                            '双方确认后可结案' if available and not requested else
                            '双方确认后可处理退款' if available else '仅供核对，带加购的追加协商确认与退款尚未开放')})
            from .negotiation_followup import followup_enabled, completed_followup_execution_enabled, preview_followup
            result = preview_followup(case.pk, actor=request.account, refund_units=requested)
            # The conservation check includes the prior person's private
            # earnings; the current case only needs its own participant terms.
            result.pop('projected_ledger', None)
            result.pop('prior_execution_id', None)
            result['read_only'] = (not followup_enabled() or
                (order.status == Order.Status.COMPLETED and not completed_followup_execution_enabled()))
            return Response({'code':0, 'data':result,
                             'msg':'仅供金额核对，追加协商方案和退款尚未开放' if result['read_only'] else '测算结果仅供核对'})
        return Response({'code':0, 'data':preview(order, proposals._scope(order, case), requested)})

    @action(detail=True, methods=['get'], url_path='negotiation-funding')
    @transaction.atomic
    @scoped_refund_case_action
    def negotiation_funding(self, request, pk=None):
        from . import negotiation_proposals as proposals
        from .negotiation_financial import preview
        from .negotiation_funding import funding_preview
        if not proposals.enabled():
            return Response({'code':404, 'msg':'协商方案暂未开放'}, status=404)
        requested = serializers.IntegerField(min_value=0).run_validation(request.query_params.get('refund_units'))
        initial = self.get_object()
        order, case = proposals._lock_case(initial.pk)
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from .models import OrderOriginalShareTransfer
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                if order.refund_records.exists():
                    if order.refund_records.count() == 2:
                        from .points_third_refund_preview import preview_locked, available_for_case
                    else:
                        from .points_transfer_followup_preview import preview_locked
                        from .points_transfer_followup_termination import available_for_case
                    from .refunds import authorized_refund
                    calculated = preview_locked(case.pk, refund_equivalent_units=requested)
                    item = calculated['provider_recovery_preview']
                    can_execute = ((available_for_case(case, refund_units=requested)
                                   if order.refund_records.count() >= 2 else available_for_case(case))
                                   and authorized_refund(request.account)
                                   and calculated['estimated_sufficient'])
                    return Response({'code': 0, 'data': {'providers': [{
                        'account_id': calculated['account_id'], 'pending_income_units': item['pending_units'],
                        'wallet_units': item['wallet_units'], 'deposit_units': item['deposit_units'],
                        'shortfall_units': item['shortfall_units']}],
                        'book_amounts_sufficient': calculated['estimated_sufficient'], 'calculated_at': timezone.now(),
                        'executable': can_execute, 'read_only_points': not can_execute, 'refund_units': requested,
                        'refund_coin_units': calculated['coin_units'],
                        'refund_points_hundredths': calculated['points_hundredths']},
                        'msg': '双方确认后可由授权客服处理' if can_execute else '仅供核对，转单后的追加协商退款尚未开放'})
                from .points_transfer_refund_preview import preview_locked
                from .points_negotiation_termination import main_refund_preview_capability
                from .refunds import authorized_refund
                from wallet.refund_recovery_order import enabled as deposit_recovery_enabled
                calculated = preview_locked(case.pk, refund_equivalent_units=requested)
                providers = [{'account_id': item['account_id'], 'pending_income_units': item['pending_units'],
                              'wallet_units': item['wallet_units'], 'deposit_units': item['deposit_units'],
                              'shortfall_units': item['shortfall_units']} for item in calculated['provider_recovery_preview']]
                check_required, path_enabled = main_refund_preview_capability(order, case)
                available = path_enabled and authorized_refund(request.account)
                sufficient = calculated['estimated_sufficient']
                needs_deposit = any(row['deposit_units'] for row in providers)
                executable = available and sufficient and (not needs_deposit or deposit_recovery_enabled())
                result = {'providers': providers, 'book_amounts_sufficient': sufficient, 'calculated_at': timezone.now(),
                    'executable': executable, 'read_only_points': not available, 'refund_units': requested,
                    'refund_coin_units': calculated['coin_units'], 'refund_points_hundredths': calculated['points_hundredths']}
                if check_required:
                    result['execution_check_required'] = True
                if check_required and len(providers) > 2:
                    message = '当前转单首退最多支持两名受影响陪玩，请核对售后范围'
                elif not available:
                    message = '仅供核对，当前转单退款暂不可执行'
                elif not sufficient:
                    message = '陪玩承担资金不足，补足后才能退款'
                elif not executable:
                    message = '本次需要扣减押金，当前押金扣款或后续补差尚未启用'
                else:
                    message = '当事人确认后可处理，执行时重新核验资金与凭证'
                return Response({'code': 0, 'data': result, 'msg': message})
            from .points_business_access import permits
            formal_followup = any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon'))
            if (requested and order.refund_records.exists()
                    and (formal_followup or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING', False))):
                from .points_followup_preview import preview_followup
                from .points_followup_termination import endpoint_available, formal_followup_funding_ready
                from .refunds import authorized_refund
                from wallet.refund_deposit import enabled as deposit_enabled
                from wallet.refund_recovery_order import tracking_enabled
                calculated = preview_followup(case.pk, refund_equivalent_units=requested)
                result = funding_preview(order, calculated['provider_terms'])
                available = (authorized_refund(request.account) and endpoint_available(
                    calculated['prior_refund_id'], prior_paid=calculated['prior_paid']))
                funding_ready = not formal_followup or formal_followup_funding_ready(calculated)
                needs_deposit = any(row['deposit_units'] for row in result['providers'])
                deposit_ready = not needs_deposit or (deposit_enabled() and tracking_enabled())
                executable = available and funding_ready and result['book_amounts_sufficient'] and deposit_ready
                result.update(refund_units=requested, refund_coin_units=calculated['coin_units'],
                              refund_points_hundredths=calculated['points_hundredths'],
                              read_only_points=not available, execution_check_required=True, executable=executable)
                if not funding_ready:
                    message = '已有陪玩结算的后续退款须先完成资金核对'
                elif not available:
                    message = '仅供核对，当前后续退款暂不可执行'
                elif not result['book_amounts_sufficient']:
                    message = '陪玩承担资金不足，补足后才能退款'
                elif not deposit_ready:
                    message = '本次需要扣减押金，当前押金扣款或后续补差尚未启用'
                else:
                    message = '当事人确认后可处理，执行时重新核验资金与凭证'
                return Response({'code': 0, 'data': result, 'msg': message})
            from .points_negotiation_preview import combined_funding_sufficient, preview_locked
            from .points_negotiation_termination import main_refund_preview_capability
            from .refunds import authorized_refund
            from wallet.refund_recovery_order import enabled as deposit_recovery_enabled
            calculated = preview_locked(case.pk, refund_equivalent_units=requested)
            result = funding_preview(order, calculated['provider_terms'])
            check_required, path_enabled = main_refund_preview_capability(order, case)
            available = path_enabled and authorized_refund(request.account)
            combined_ready = (not permits(order, phase='refund', kind='addon')
                              or combined_funding_sufficient(calculated))
            needs_deposit = any(row['deposit_units'] for row in result['providers'])
            executable = (available and result['book_amounts_sufficient'] and combined_ready
                          and (not needs_deposit or deposit_recovery_enabled()))
            result.update(refund_units=requested, refund_coin_units=calculated['coin_units'],
                          refund_points_hundredths=calculated['points_hundredths'],
                          read_only_points=not available, executable=executable)
            if check_required:
                result['execution_check_required'] = True
            if calculated.get('conditional_combined_funding') is not None:
                from dataclasses import asdict
                result['conditional_combined_funding'] = asdict(calculated['conditional_combined_funding'])
            if not available:
                message = '仅供核对，当前退款暂不可执行'
            elif not result['book_amounts_sufficient']:
                message = '陪玩承担资金不足，补足后才能退款'
            elif not combined_ready:
                message = '主单与加购的到账补资不足，补足后才能退款'
            elif not executable:
                message = '本次需要扣减押金，当前押金扣款或后续补差尚未启用'
            else:
                message = '当事人确认后可处理，执行时重新核验资金与凭证'
            return Response({'code': 0, 'data': result, 'msg': message})
        from .negotiation_zero import marked_execution_id
        followup = (order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
                    or marked_execution_id(order) is not None)
        from .ordinary_addon_followup_preview import applies, preview_case_locked
        if followup and applies(order):
            from .settlement import SettlementError
            from .ordinary_addon_followup_termination import endpoint_enabled, zero_enabled
            from .refunds import authorized_refund
            try:
                estimate = preview_case_locked(case.pk, refund_units=requested)
            except SettlementError as exc:
                raise serializers.ValidationError(str(exc)) from exc
            return Response({'code': 0, 'data': {'providers': [{'account_id': estimate.account_id,
                'pending_income_units': estimate.pending_deduction, 'wallet_units': estimate.wallet_deduction,
                'deposit_units': estimate.deposit_deduction, 'shortfall_units': estimate.insufficient}],
                'book_amounts_sufficient': estimate.insufficient == 0, 'calculated_at': timezone.now(),
                'executable': bool((requested or zero_enabled()) and not estimate.insufficient and endpoint_enabled(order)
                                   and authorized_refund(request.account)),
                'read_only_followup': not endpoint_enabled(order), 'refund_units': requested,
                'store_funding': {'required_units': estimate.required_funding,
                    'allocated_units': estimate.allocated_funding, 'release_units': estimate.funding_to_release,
                    'remaining_units': estimate.funding_still_needed,
                    'conditional_on_provider_funding': bool(estimate.insufficient)}},
                'msg': ('陪玩承担资金不足，补足后才能退款' if endpoint_enabled(order) and estimate.insufficient else
                        '双方确认后可处理' if (requested or zero_enabled()) and endpoint_enabled(order) else '仅供核对，带加购的追加协商退款尚未开放')})
        if followup:
            from .negotiation_followup import preview_followup
            calculated = preview_followup(case.pk, actor=request.account, refund_units=requested)
        else:
            calculated = preview(order, proposals._scope(order, case), requested)
        result = funding_preview(order, calculated['provider_terms'])
        result['refund_units'] = requested
        result['read_only_followup'] = followup
        return Response({'code':0, 'data':result})

    @action(detail=True, methods=['get', 'post'], url_path='subsidy-negotiation-execution')
    def subsidy_negotiation_execution(self, request, pk=None):
        """Execute an agreed zero-payment safety-box termination, or read it back."""
        from .subsidy_execution import execute, table_available, verify_execution
        if not table_available():
            return Response({'code':503, 'msg':'安全箱协商记录尚未就绪'}, status=503)
        data = request.query_params if request.method == 'GET' else request.data
        if set(data) != {'proposal_id', 'client_request_id'}:
            raise serializers.ValidationError('协商执行请求格式无效')
        raw_id = data.get('proposal_id')
        if request.method == 'POST' and type(raw_id) is not int:
            raise serializers.ValidationError('协商方案编号无效')
        proposal_id = serializers.IntegerField(min_value=1).run_validation(raw_id)
        request_id = data.get('client_request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id.strip() != request_id:
            raise serializers.ValidationError('操作凭证无效')
        initial = self.get_object()
        if request.method == 'POST':
            receipt = execute(initial.pk, proposal_id, operator=request.account,
                              legacy_operator=request.legacy_user, request_id=request_id)
            request._request._business_audit_recorded = True
        else:
            from .financial_lock import lock_existing_order_money
            from .models import SubsidyAgreementExecution
            from .negotiation_proposals import _lock_case
            with transaction.atomic():
                order, case = _lock_case(initial.pk)
                lock_existing_order_money()
                receipt = SubsidyAgreementExecution.objects.select_for_update().filter(
                    order=order, proposal_id=proposal_id, proposal__case=case).first()
                if not receipt:
                    return Response({'code':0, 'data':{'status':'NOT_FOUND'}, 'msg':'未查到协商执行记录'})
                if receipt.operator_id != request.account.pk or receipt.request_id != request_id:
                    raise serializers.ValidationError('操作凭证不匹配，请联系原经办人核对')
                verify_execution(receipt)
        return Response({'code':0, 'data':{'status':'COMPLETED', 'execution_id':receipt.pk,
            'order_id':receipt.order_id, 'proposal_id':receipt.proposal_id, 'refund_units':0,
            'effective_at':receipt.accepted_at, 'processed_at':receipt.processed_at,
            'client_request_id':receipt.request_id, 'shares':receipt.shares}, 'msg':'安全箱协商已执行，收益按规则等待结算'})

    @action(detail=True, methods=['get', 'post'], url_path='negotiation-execution')
    @protect_referral_execution
    @scoped_refund_case_action
    def negotiation_execution(self, request, pk=None):
        """Execute or reconcile the same staff refund request within its store."""
        from . import negotiation_proposals as proposals
        data = request.query_params if request.method == 'GET' else request.data
        enabled = proposals.enabled() and getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False)
        legacy_execution_columns = False
        if not enabled:
            # Existing operations must remain reconcilable after either switch
            # is turned off; older stores may not have these tables at all.
            if request.method != 'GET' or set(data) != {'proposal_id', 'client_request_id'}:
                return Response({'code':404, 'msg':'协商退款暂未开放'}, status=404)
            from .models import NegotiationDecision, NegotiationExecution, NegotiationExecutionShare, NegotiationProposal
            connection = transaction.get_connection()
            with connection.cursor() as cursor:
                tables = set(connection.introspection.table_names(cursor))
            if not {NegotiationExecution._meta.db_table, NegotiationExecutionShare._meta.db_table,
                    NegotiationProposal._meta.db_table, NegotiationDecision._meta.db_table}.issubset(tables):
                return Response({'code':404, 'msg':'协商退款暂未开放'}, status=404)
            with connection.cursor() as cursor:
                columns = {item.name for item in connection.introspection.get_table_description(
                    cursor, NegotiationExecution._meta.db_table)}
            legacy_execution_columns = not {'zero_order_id', 'zero_request_id', 'zero_allocation'}.issubset(columns)
        if request.method == 'GET' and not data:
            case = self.get_object()
            _, latest = proposals.latest_for_case(case)
            from .negotiation_zero import marked_execution_id
            from .negotiation_followup import followup_enabled, completed_followup_execution_enabled
            from .points_service_payment import needs_integrated_processing
            from .points_negotiation_termination import available_for_order as points_termination_available
            points_pending = needs_integrated_processing(case.order)
            from .models import OrderOriginalShareTransfer
            transferred = points_pending and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=case.order).exists()
            followup = (case.order.payment_status in (Order.PaymentStatus.PARTIALLY_REFUNDED,
                                                       Order.PaymentStatus.REFUNDED)
                        or marked_execution_id(case.order) is not None)
            points_execute = points_termination_available(case.order)
            if points_pending and latest and getattr(getattr(latest, 'points_terms', None), 'prior_refund_id', None):
                if transferred:
                    if case.order.refund_records.count() >= 3:
                        from .points_round_refund_preview import available_for_case
                        points_execute = available_for_case(case, refund_units=latest.refund_units)
                    elif case.order.refund_records.count() == 2:
                        from .points_third_refund_preview import available_for_case
                        points_execute = available_for_case(case, refund_units=latest.refund_units)
                    else:
                        from .points_transfer_followup_termination import available_for_case
                        points_execute = available_for_case(case)
                else:
                    from .points_followup_termination import endpoint_available
                    points_execute = endpoint_available(latest.points_terms.prior_refund_id)
            from .ordinary_addon_followup_preview import applies as addon_followup
            from .ordinary_addon_followup_termination import endpoint_enabled as addon_endpoint_enabled
            from .ordinary_addon_followup_termination import endpoint_available as addon_endpoint_available
            addon_ready = (addon_endpoint_available(case) if case.status == 'OPEN' else addon_endpoint_enabled(case.order))
            ordinary_addon_waiting = (followup and not points_pending and addon_followup(case.order) and not addon_ready)
            return Response({'code':0, 'data':{'proposal':proposals.serialize(latest, actor=request.account, staff=True)
                                              if latest else None,
                                              'execution_enabled':(points_execute if points_pending else (addon_ready
                                                  if followup and addon_followup(case.order) else (not ordinary_addon_waiting and (not followup or (followup_enabled() and not
                                                  (case.status == 'OPEN' and case.order.status == Order.Status.COMPLETED
                                                   and not completed_followup_execution_enabled())))))),
                                              'zero_execution_enabled':getattr(settings, 'NEGOTIATION_ZERO_EXECUTION_ENABLED', False)}})
        if set(data) != {'proposal_id', 'client_request_id'}:
            raise serializers.ValidationError('协商退款请求格式无效')
        raw_id = data.get('proposal_id')
        if request.method == 'POST' and type(raw_id) is not int:
            raise serializers.ValidationError('协商方案编号无效')
        proposal_id = serializers.IntegerField(min_value=1).run_validation(raw_id)
        request_id = data.get('client_request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip():
            raise serializers.ValidationError('退款操作凭证无效')
        initial = self.get_object()
        from .points_service_payment import needs_integrated_processing
        from .points_negotiation_termination import enabled as points_termination_enabled
        points_order = needs_integrated_processing(initial.order)
        if points_order:
            from .models import NegotiationProposal, PointsZeroFollowupTermination
            zero_proposal = NegotiationProposal.objects.filter(pk=proposal_id, case=initial, refund_units=0).first()
            if zero_proposal:
                from .points_zero_followup import execute_locked, verify_record_locked
                if request.method == 'POST':
                    receipt = execute_locked(initial.pk, proposal_id, operator=request.account,
                                             legacy_operator=request.legacy_user, request_id=request_id)
                    request._request._business_audit_recorded = True
                else:
                    with transaction.atomic():
                        proposals._lock_case(initial.pk)
                        receipt = PointsZeroFollowupTermination.objects.select_for_update().filter(
                            proposal_id=proposal_id, order=initial.order).first()
                        if not receipt:
                            return Response({'code': 0, 'data': {'status': 'NOT_FOUND'}, 'msg': '未查到协商结案记录'})
                        if receipt.operator_id != request.account.pk or receipt.request_id != request_id:
                            raise serializers.ValidationError('协商操作凭证不匹配')
                        verify_record_locked(receipt)
                return Response({'code': 0, 'data': {'status': 'COMPLETED', 'execution_id': receipt.pk,
                    'order_id': receipt.order_id, 'refund_id': None, 'refund_units': 0,
                    'processed_at': receipt.terminated_at, 'client_request_id': receipt.request_id},
                    'msg': '协商结案已完成'})
        if not points_order:
            from .models import OrdinaryTransferFollowupRound
            later_round = OrdinaryTransferFollowupRound.objects.filter(
                order=initial.order, proposal_id=proposal_id, proposal__case=initial).select_related('execution__refund').first()
            if later_round:
                from .ordinary_transfer_capability import enabled as ordinary_transfer_enabled
                if not ordinary_transfer_enabled():
                    raise serializers.ValidationError('普通转单追加协商尚未开放')
                if request.method == 'POST':
                    from .ordinary_transfer_followup_termination import execute_and_close
                    from .settlement import SettlementError
                    try:
                        receipt = execute_and_close(initial.pk, proposal_id, operator=request.account,
                            legacy_operator=request.legacy_user, request_id=request_id)
                    except (SettlementError, WalletAddressingError, ValueError) as exc:
                        raise serializers.ValidationError('追加退款资金或结算凭证待核对') from exc
                    request._request._business_audit_recorded = True
                else:
                    receipt = later_round.execution
                    if not receipt:
                        return Response({'code': 0, 'data': {'status': 'NOT_FOUND'}, 'msg': '未查到退款记录'})
                    receipt_key = receipt.refund.client_request_id if receipt.refund_id else receipt.zero_request_id
                    if receipt.operator_id != request.account.pk or receipt_key != request_id:
                        raise serializers.ValidationError('退款操作凭证不匹配')
                    with transaction.atomic():
                        if receipt.refund_id:
                            from .negotiation_execution import verify_execution
                            from .negotiation_termination import verify_termination_evidence
                        else:
                            from .ordinary_transfer_followup_zero import verify_execution, verify_termination as verify_termination_evidence
                        from .ordinary_transfer_followup_revision import verify_locked
                        proposals._lock_case(initial.pk)
                        from .financial_lock import lock_order_money
                        lock_order_money()
                        verify_execution(receipt)
                        verify_termination_evidence(receipt)
                        verify_locked(later_round.settlement_revision)
                return Response({'code': 0, 'data': {'status': 'COMPLETED', 'execution_id': receipt.pk,
                    'order_id': receipt.refund.order_id if receipt.refund_id else receipt.zero_order_id,
                    'refund_id': receipt.refund_id, 'refund_units': receipt.refund.amount if receipt.refund_id else 0,
                    'processed_at': receipt.termination_processed_at,
                    'client_request_id': receipt.refund.client_request_id if receipt.refund_id else receipt.zero_request_id},
                    'msg': '协商退款已完成' if receipt.refund_id else '协商结案已完成'})
        from .ordinary_addon_followup_preview import applies as addon_followup
        if not points_order and addon_followup(initial.order):
            from .models import NegotiationExecution, NegotiationFollowupLink
            from .ordinary_addon_followup_settlement import is_followup
            from .ordinary_addon_followup_termination import endpoint_enabled, execute_and_close
            link = NegotiationFollowupLink.objects.filter(order=initial.order, execution__proposal_id=proposal_id,
                                                         execution__proposal__case=initial).first()
            prior = NegotiationExecution.objects.filter(proposal_id=proposal_id, proposal__case=initial).first()
            if link and not is_followup(link):
                raise serializers.ValidationError('追加协商凭证待核对')
            if not prior or link:
                if request.method == 'POST':
                    if not endpoint_enabled(initial.order):
                        raise serializers.ValidationError('带加购的追加协商退款尚未开放')
                    from .settlement import SettlementError
                    try:
                        link = execute_and_close(initial.pk, proposal_id, operator=request.account,
                            legacy_operator=request.legacy_user, request_id=request_id)
                    except (SettlementError, WalletAddressingError, ValueError) as exc:
                        raise serializers.ValidationError('退款资金或押金凭证待核对') from exc
                    request._request._business_audit_recorded = True
                else:
                    if not link:
                        return Response({'code': 0, 'data': {'status': 'NOT_FOUND'}, 'msg': '未查到退款记录'})
                    from .ordinary_addon_followup_posting import verify_posting_locked
                    from .negotiation_followup_accounting import verify_followup_termination
                    from .settlement import SettlementError
                    try:
                        with transaction.atomic():
                            order, case = proposals._lock_case(initial.pk)
                            from .financial_lock import lock_existing_order_money
                            lock_existing_order_money()
                            link = verify_posting_locked(link)
                            verify_followup_termination(link)
                            own_key = (link.execution.refund.client_request_id if link.execution.refund_id
                                       else link.execution.zero_request_id)
                            if link.execution.operator_id != request.account.pk or own_key != request_id:
                                raise serializers.ValidationError('退款操作凭证不匹配')
                    except (SettlementError, ValueError) as exc:
                        raise serializers.ValidationError('退款凭证待核对') from exc
                receipt = link.execution
                zero = not receipt.refund_id
                return Response({'code': 0, 'data': {'status': 'COMPLETED', 'execution_id': receipt.pk,
                    'order_id': receipt.zero_order_id if zero else receipt.refund.order_id, 'refund_id': receipt.refund_id,
                    'refund_units': 0 if zero else receipt.refund.amount, 'processed_at': receipt.termination_processed_at,
                    'client_request_id': receipt.zero_request_id if zero else receipt.refund.client_request_id},
                    'msg': '协商结案已完成' if zero else '协商退款已完成'})
        points_readback = False
        if points_order and request.method == 'GET' and not points_termination_enabled():
            from .models import PointsNegotiationPosting
            connection = transaction.get_connection()
            with connection.cursor() as cursor:
                tables = set(connection.introspection.table_names(cursor))
            if PointsNegotiationPosting._meta.db_table in tables:
                points_readback = PointsNegotiationPosting.objects.filter(proposal_id=proposal_id,
                    proposal__case=initial).exists()
        if points_order and (points_termination_enabled() or points_readback):
            from .models import PointsNegotiationPosting, PointsNegotiationTerms
            from .points_negotiation_termination import execute_and_terminate as execute_points, verify_termination_locked
            is_points_followup = PointsNegotiationTerms.objects.filter(proposal_id=proposal_id,
                proposal__case=initial, prior_refund__isnull=False).exists()
            if request.method == 'POST':
                if is_points_followup:
                    from .models import OrderOriginalShareTransfer
                    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=initial.order).exists():
                        from .points_transfer_followup_termination import endpoint_enabled, available_for_case, execute_locked
                        from .models import PointsRefundRoundEvidence, OrderPointsRefund
                        round_context = PointsNegotiationTerms.objects.filter(proposal_id=proposal_id,
                            proposal__case=initial).values_list('round_context', flat=True).first()
                        third_done = PointsRefundRoundEvidence.objects.filter(order=initial.order,
                            posting__proposal_id=proposal_id, posting__proposal__case=initial).exists()
                        prior_id = PointsNegotiationTerms.objects.filter(proposal_id=proposal_id,
                            proposal__case=initial).values_list('prior_refund_id', flat=True).first()
                        third_pending = bool(initial.order.refund_records.count() == 2 and prior_id
                            and OrderPointsRefund.objects.filter(redemption__order=initial.order,
                                refund_id__lte=OrderPointsRefund.objects.filter(pk=prior_id)
                                    .values('refund_id')[:1]).count() == 2)
                        if round_context is not None:
                            from .points_round_refund_termination import execute_locked as execute_round
                            if not endpoint_enabled():
                                raise serializers.ValidationError('多轮积分退款尚未开放')
                            receipt = execute_round(initial.pk, proposal_id, operator=request.account,
                                                    legacy_operator=request.legacy_user, request_id=request_id)
                        elif third_done or third_pending:
                            from .points_third_refund_termination import execute_locked as execute_third
                            if not endpoint_enabled():
                                raise serializers.ValidationError('第三轮积分退款尚未开放')
                            receipt = execute_third(initial.pk, proposal_id, operator=request.account,
                                                    legacy_operator=request.legacy_user, request_id=request_id)
                        else:
                            existing = PointsNegotiationPosting.objects.filter(proposal_id=proposal_id,
                                                                                proposal__case=initial).exists()
                            if not endpoint_enabled() or (not existing and not available_for_case(initial)):
                                raise serializers.ValidationError('转单后的追加积分退款尚未开放')
                            receipt = execute_locked(initial.pk, proposal_id, operator=request.account,
                                                     legacy_operator=request.legacy_user, request_id=request_id)
                    else:
                        from .points_followup_termination import endpoint_available, execute_followup_and_close
                        prior_id = PointsNegotiationTerms.objects.filter(proposal_id=proposal_id,
                            proposal__case=initial).values_list('prior_refund_id', flat=True).first()
                        if not endpoint_available(prior_id):
                            raise serializers.ValidationError('后续积分协商退款尚未开放')
                        receipt = execute_followup_and_close(initial.pk, proposal_id, operator=request.account,
                                                             legacy_operator=request.legacy_user, request_id=request_id)
                else:
                    from .models import OrderOriginalShareTransfer
                    from .points_negotiation_termination import available_for_order, transfer_endpoint_enabled
                    transferred = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=initial.order).exists()
                    if transferred:
                        existing = PointsNegotiationPosting.objects.filter(proposal_id=proposal_id,
                                                                            proposal__case=initial).exists()
                        if not transfer_endpoint_enabled() or (not existing and not available_for_order(initial.order)):
                            raise serializers.ValidationError('转单后的积分退款结案暂未开放')
                    receipt = execute_points(initial.pk, proposal_id, operator=request.account,
                                             legacy_operator=request.legacy_user, request_id=request_id,
                                             _transfer_testing=transferred)
                request._request._business_audit_recorded = True
            else:
                with transaction.atomic():
                    order, case = proposals._lock_case(initial.pk)
                    from .financial_lock import lock_existing_order_money
                    lock_existing_order_money()
                    if is_points_followup and getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False):
                        from .points_receipt_usage import prelock_followup_receipts
                        prelock_followup_receipts(order)
                    if not is_points_followup and getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
                        from .points_receipt_usage import prelock_transfer_refund_receipts
                        prelock_transfer_refund_receipts(order)
                    receipt = PointsNegotiationPosting.objects.select_for_update().filter(
                        proposal_id=proposal_id, proposal__case=case).first()
                    if not receipt:
                        from .models import OrderRefund
                        if OrderRefund.objects.filter(order=order, client_request_id=request_id).exists():
                            raise serializers.ValidationError('操作凭证已用于其他退款')
                        return Response({'code':0, 'data':{'status':'NOT_FOUND'}, 'msg':'未查到退款记录，请使用原操作凭证重试'})
                    from .models import OrderOriginalShareTransfer
                    transferred_followup = is_points_followup and OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists()
                    if is_points_followup and not transferred_followup:
                        from .points_followup_termination import require_followup_request_locked
                        require_followup_request_locked(receipt, request_id)
                    if receipt.operator_id != request.account.pk or receipt.points_refund.refund.client_request_id != request_id:
                        raise serializers.ValidationError('退款操作凭证不匹配，请联系原经办人核对')
                    if is_points_followup:
                        if transferred_followup:
                            from .models import PointsRefundRoundEvidence
                            if (getattr(getattr(receipt.proposal, 'points_terms', None), 'round_context', None)
                                    is not None):
                                from .points_round_refund_termination import verify_completed_locked
                                receipt = verify_completed_locked(order, case, receipt, operator=request.account,
                                    legacy_operator=request.legacy_user, request_id=request_id)
                            elif PointsRefundRoundEvidence.objects.filter(order=order, posting=receipt).exists():
                                from .points_third_refund_termination import _verify_completed_locked
                                receipt = _verify_completed_locked(order, case, receipt, operator=request.account,
                                    legacy_operator=request.legacy_user, request_id=request_id)
                            else:
                                from .points_transfer_followup_termination import verify_closed_or_settled_locked
                                receipt = verify_closed_or_settled_locked(receipt)
                        else:
                            from .points_followup_termination import verify_followup_termination_locked
                            receipt = verify_followup_termination_locked(receipt)
                    else:
                        receipt = verify_termination_locked(receipt, allow_verified_later_final=True)
            proof, refund = receipt.points_refund, receipt.points_refund.refund
            return Response({'code':0, 'data':{'status':'COMPLETED', 'execution_id':receipt.pk,
                'order_id':refund.order_id, 'refund_id':refund.pk, 'refund_units':refund.amount,
                'refund_equivalent_units':proof.equivalent_units, 'refund_coin_units':proof.coin_units,
                'refund_points_hundredths':proof.points_hundredths, 'processed_at':receipt.termination_processed_at,
                'client_request_id':refund.client_request_id}, 'msg':'积分协商退款已完成'})
        if request.method == 'POST':
            from .negotiation_termination import execute_and_terminate
            from .settlement import SettlementError
            try:
                receipt = execute_and_terminate(initial.pk, proposal_id, operator=request.account,
                                               legacy_operator=request.legacy_user, request_id=request_id)
            except (SettlementError, WalletAddressingError, ValueError) as exc:
                raise serializers.ValidationError('退款资金或押金凭证待核对') from exc
            request._request._business_audit_recorded = True
        else:
            from .models import NegotiationExecution, NegotiationProposal, OrderRefund
            from .financial_lock import lock_existing_order_money
            from .negotiation_execution import verify_execution
            with transaction.atomic():
                order, case = proposals._lock_case(initial.pk)
                if not NegotiationProposal.objects.filter(pk=proposal_id, case=case).exists():
                    raise serializers.ValidationError('协商方案不存在，请刷新核对')
                query = NegotiationExecution.objects.filter(proposal_id=proposal_id, proposal__case=case)
                if legacy_execution_columns:
                    query = query.defer('zero_order', 'zero_request_id', 'zero_allocation')
                receipt = query.first()
                if not receipt:
                    if (OrderRefund.objects.filter(order=order, client_request_id=request_id).exists()
                            or (not legacy_execution_columns and NegotiationExecution.objects.filter(
                                zero_order=order, zero_request_id=request_id).exists())):
                        raise serializers.ValidationError('操作凭证已用于其他协商处理')
                    return Response({'code':0, 'data':{'status':'NOT_FOUND'}, 'msg':'未查到退款记录，请使用原操作凭证重试'})
                from .settlement import SettlementError
                try:
                    lock_existing_order_money()
                    from .negotiation_execution import _lock_reverse_addon_people
                    _lock_reverse_addon_people(order)
                    query = NegotiationExecution.objects.select_for_update()
                    if legacy_execution_columns:
                        query = query.defer('zero_order', 'zero_request_id', 'zero_allocation')
                    receipt = query.get(pk=receipt.pk)
                    from .negotiation_followup_accounting import (marked_followup_id, verify_followup_accounting,
                                                                  verify_followup_termination)
                    followup_id = marked_followup_id(order)
                    if followup_id is not None:
                        from .models import NegotiationFollowupLink
                        link = NegotiationFollowupLink.objects.select_for_update().filter(pk=followup_id, order=order).first()
                        if not link or receipt.pk not in (link.previous_id, link.execution_id):
                            raise serializers.ValidationError('追加协商凭证待核对')
                        own_key = receipt.refund.client_request_id if receipt.refund_id else receipt.zero_request_id
                        if receipt.operator_id != request.account.pk or own_key != request_id:
                            raise serializers.ValidationError('操作凭证不匹配，请联系原经办人核对')
                        from .ordinary_addon_followup_settlement import is_followup
                        if is_followup(link):
                            from .ordinary_addon_followup_posting import verify_posting_locked
                            verify_posting_locked(link)
                        else:
                            verify_followup_accounting(link)
                        verify_followup_termination(link)
                    elif not receipt.refund_id:
                        if receipt.operator_id != request.account.pk or receipt.zero_request_id != request_id:
                            raise serializers.ValidationError('操作凭证不匹配，请联系原经办人核对')
                        from .negotiation_zero import verify_zero_execution, verify_zero_termination
                        verify_zero_execution(receipt)
                        verify_zero_termination(receipt)
                    else:
                        if receipt.operator_id != request.account.pk or receipt.refund.client_request_id != request_id:
                            raise serializers.ValidationError('退款操作凭证不匹配，请联系原经办人核对')
                        verify_execution(receipt)
                        if order.addon_intents.filter(payment__refund_records__created_at__lt=receipt.created_at).exists():
                            from .ordinary_addon_prior_refund import verify_locked
                            verify_locked(order, boundary=receipt.created_at)
                        if not receipt.termination_processed_at:
                            raise serializers.ValidationError('协商退款尚待核对')
                        from .negotiation_termination import verify_termination_evidence
                        verify_termination_evidence(receipt)
                except (SettlementError, ValueError) as exc:
                    raise serializers.ValidationError('协商退款凭证待核对') from exc
        zero = not receipt.refund_id
        return Response({'code':0, 'data':{'status':'COMPLETED', 'execution_id':receipt.pk,
            'order_id':receipt.zero_order_id if zero else receipt.refund.order_id, 'refund_id':receipt.refund_id,
            'refund_units':0 if zero else receipt.refund.amount, 'processed_at':receipt.termination_processed_at,
            'client_request_id':receipt.zero_request_id if zero else receipt.refund.client_request_id},
            'msg':'协商终止已完成' if zero else '协商退款已完成'})

    @action(detail=False, methods=['post'], url_path='open-negotiation')
    @protect_open(staff=True)
    @transaction.atomic
    @scoped_transfer_open_action
    def open_negotiation(self, request):
        """Record a customer's request for an in-service negotiation, not agreement or refund."""
        order_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('order_id'))
        reason = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('reason'))
        request_id = serializers.CharField(min_length=1, max_length=64).run_validation(request.data.get('client_request_id'))
        scope = normalize_scope(request.data.get('affected_provider_account_ids'))
        requested_segments = normalize_scope(request.data.get('affected_segment_ids'))
        addon_payment_id = request.data.get('addon_payment_id')
        if addon_payment_id is not None and (type(addon_payment_id) is not int or addon_payment_id <= 0):
            raise serializers.ValidationError('请选择有效的加购付款')
        if not scope:
            raise serializers.ValidationError('请选择受影响的陪玩')
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        consultation = None
        consultation_hint = getattr(request, '_consultation_aftersale_hint', None)
        if consultation_hint is not None:
            from chat.aftersale_context import lock_context, verify_order
            from .financial_lock import lock_order_money
            # Enter the order's funding scope before locking the chat owner.
            lock_order_money()
            message_id, hint = consultation_hint
            context = lock_context(hint, message_id, request.account)
            consultation = verify_order(context, order, scope)
        case = order.aftersales.select_for_update().first()
        prior = next((entry for entry in case.history if entry.get('client_request_id') == request_id), None) if case else None
        if prior:
            if (prior.get('reason') != reason or prior.get('affected_provider_account_ids') != scope
                    or (requested_segments is not None and prior.get('affected_segment_ids') != requested_segments)
                    or prior.get('addon_payment_id') != addon_payment_id
                    or prior.get('consultation_message_id') != (consultation.get('message_id') if consultation else None)
                    or prior.get('operator_id') != request.account.pk):
                return Response({'code': 409, 'errorCode': 'AFTERSALE_REQUEST_CONFLICT',
                                 'msg': '操作凭证已用于其他售后申请，请刷新核对'}, status=409)
            if case.client_request_id != request_id:
                return Response({'code': 409, 'errorCode': 'AFTERSALE_REQUEST_PREVIOUS',
                                 'msg': '这次申请已处理，当前售后记录已更新，请刷新核对'}, status=409)
            return Response({'code': 0, 'data': self.get_serializer(case).data, 'msg': '售后申请已登记'})
        if case and case.status == 'OPEN':
            return Response({'code': 409, 'errorCode': 'AFTERSALE_ALREADY_OPEN',
                             'msg': '已有售后处理中，请查看原记录'}, status=409)
        from .aftersale_segment_scope import resolve_locked as resolve_segments
        segments = resolve_segments(order, scope, requested_segments) if addon_payment_id is None else None
        addon_only = False
        if addon_payment_id is not None:
            from .financial_lock import lock_order_money
            from .models import NegotiationExecution, OrderAddonPayment
            from .ordinary_addon_production import eligible as ordinary_addon_eligible
            lock_order_money()
            payment = OrderAddonPayment.objects.select_for_update().select_related('intent').filter(
                pk=addon_payment_id, intent__order=order).first()
            deadline = aftersale_deadline(order)
            eligible_state = ((order.required_provider_count == 1 and order.status == Order.Status.COMPLETED
                               and deadline and timezone.now() < deadline)
                              or (order.required_provider_count == 2 and order.status == Order.Status.IN_SERVICE))
            from .points_business_access import permits as points_permits
            points_addon = points_permits(order, phase='refund', kind='addon')
            if points_addon:
                from .points_third_addon_refund import active as third_addon_refund_active
                third_addon = bool(payment and third_addon_refund_active(order, payment))
                from .models import OrderAddonFundingReleaseRun
                existing_team = OrderAddonFundingReleaseRun.objects.filter(
                    handover__payment__intent__order=order, points_handover_snapshot__kind='SAME_TEAM_V1').first()
                payment_team = (OrderAddonFundingReleaseRun.objects.filter(handover__payment=payment,
                    points_handover_snapshot__kind='SAME_TEAM_V1').exists() if payment else False)
                from .points_team_other_addon_refund import continuing_purchase
                postmain_purchase = bool(payment and order.refund_records.exists()
                                         and continuing_purchase(order, payment))
                if existing_team and not payment_team and not third_addon:
                    from .points_team_other_addon_refund import continuing_purchase
                    if (not payment or order.refund_records.count() not in (0, 1, 2) or payment.handovers.exists()
                            or (order.refund_records.exists() and payment.buyer_transaction.created_at >=
                                order.refund_records.order_by('created_at', 'pk').first().created_at
                                and not continuing_purchase(order, payment))):
                        raise serializers.ValidationError('同单接替后其他加购的退款范围待核对')
                eligible_state = (order.status == Order.Status.IN_SERVICE or
                    (order.status == Order.Status.COMPLETED and deadline and timezone.now() < deadline))
                eligible_accounts = {payment.intent.provider_id} if payment else set()
                team_run = None
                if payment and payment.handovers.exists():
                    from .models import OrderOriginalShareTransfer
                    from .points_transfer_refund_access import first_handover_refund, single_post_refund_handover, post_refund_handover_history
                    from .points_followup_handover_invalidation import other_pending_payment_id
                    from .models import OrderAddonFundingReleaseRun
                    transfers = list(OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('pk'))
                    team_run = OrderAddonFundingReleaseRun.objects.filter(handover__payment=payment,
                        points_handover_snapshot__kind='SAME_TEAM_V1').first() if not transfers else None
                    other_pending = other_pending_payment_id(order, payment.pk)
                    from .points_transfer_followup_addon_version import refund_preflight_active
                    if not team_run and not (first_handover_refund(order, transfers)
                                             or single_post_refund_handover(order, pending_payment_id=other_pending)
                                             or post_refund_handover_history(order, pending_payment_id=other_pending)
                                             or refund_preflight_active(order, payment) or third_addon):
                        raise serializers.ValidationError('该加购接替后的退款范围尚未接通')
                    if team_run:
                        from .addon_funding_release import verify_run
                        verify_run(team_run)
                    from .addon_handover import assert_no_pending_handover, current_responsibilities
                    if third_addon:
                        from .points_third_addon_responsibilities import current as current_responsibilities
                    from .points_followup_handover_invalidation import may_refund_pending_target
                    from .points_handover_refund_invalidation import permits_other_payment_locked
                    team_pending = permits_other_payment_locked(order, payment.pk, allow_same_payment=True)
                    from .points_third_handover_invalidation import (permits_locked as permits_third_invalidation,
                        permits_for_round_locked)
                    pending = payment.handovers.select_for_update().filter(status='PENDING').first()
                    third_pending = bool(third_addon and pending and (
                        permits_for_round_locked(order, pending) if order.refund_records.count() >= 4
                        else permits_third_invalidation(order, pending)))
                    if not may_refund_pending_target(order, payment) and not team_pending and not third_pending:
                        assert_no_pending_handover(payment)
                    _, responsibilities = current_responsibilities(payment, lock=True)
                    eligible_accounts = {row['provider_id'] for row in responsibilities if row['basis'] > 0}
                settled_unaffected = False
                from .models import PointsZeroFollowupTermination
                transfer_zero = PointsZeroFollowupTermination.objects.filter(order=order,
                    terminated_at__isnull=False).select_related('assignment').first()
                if (transfer_zero and isinstance(transfer_zero.funding_snapshot, dict)
                        and transfer_zero.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1'):
                    if not payment or scope != [transfer_zero.assignment.provider_account_id]:
                        raise serializers.ValidationError('零退款终止后仅能选择未结算的实际履约人')
                    from .points_transfer_zero_addon_settlement import guard_unpaid_participants_locked
                    guard_unpaid_participants_locked(payment, [{'account_id': account} for account in scope])
                    settled_unaffected = True
                if (payment and (not payment.handovers.exists() or team_run)
                        and order.status == Order.Status.COMPLETED and not order.settled_at
                        and len(scope) == 1 and scope[0] in eligible_accounts and not transfer_zero):
                    from .points_zero_followup import applies as points_zero_applies
                    from .negotiation_termination import continuing_team
                    from .models import OrderPointsRefundSettlementPerson
                    paid_people = list(OrderPointsRefundSettlementPerson.objects.filter(
                        settlement__posting__points_refund__redemption__order=order)
                        .values_list('segment__provider_account_id', flat=True))
                    active = list(order.providers.filter(released_at__isnull=True, settled_at__isnull=True)
                                  .values_list('provider_account_id', flat=True))
                    settled_unaffected = bool(points_zero_applies(order) and len(paid_people) == 1
                        and active == scope and paid_people[0] != scope[0]
                        and continuing_team(order, verify=True, allow_completed=True))
                from .points_transfer_followup_addon_version import refund_preflight_active
                followup_addon = bool(payment and (refund_preflight_active(order, payment) or third_addon))
                paid_followup_addon = False
                if payment and order.refund_records.count() == 2:
                    from .models import OrderPointsFollowupFundingRevision
                    revision = OrderPointsFollowupFundingRevision.objects.filter(
                        posting__points_refund__redemption__order=order, paid_person__isnull=False).first()
                    if revision:
                        shares = list(revision.posting.shares.select_related('assignment'))
                        paid_followup_addon = bool(len(shares) == 1 and scope == [shares[0].account_id]
                            and shares[0].account_id in eligible_accounts
                            and not shares[0].assignment.settled_at
                            and revision.paid_person.segment.provider_account_id != shares[0].account_id)
                if followup_addon:
                    from .points_transfer_zero_addon_settlement import guard_unpaid_participants_locked
                    guard_unpaid_participants_locked(payment, [{'account_id': account} for account in scope])
                addon_only = bool(payment and eligible_state and not order.settled_at
                    and (settled_unaffected or followup_addon or paid_followup_addon
                         or not order.providers.filter(settled_at__isnull=False).exists())
                    and (third_addon or order.refund_records.count() <= (2 if existing_team or postmain_purchase or followup_addon
                                                          or paid_followup_addon else 1))
                    and sum(row.amount - row.refunded_amount for row in payment.allocations.all()) > 0
                    and set(scope).issubset(eligible_accounts))
                if not addon_only:
                    raise serializers.ValidationError('当前积分加购售后范围不可用')
                from .addon_payment import verify_receipt
                verify_receipt(payment)
                if order.refund_records.exists():
                    from .models import PointsNegotiationPosting
                    from .points_negotiation_termination import verify_termination_locked
                    postings = list(PointsNegotiationPosting.objects.filter(
                        points_refund__redemption__order=order).order_by('created_at', 'pk'))
                    if (not postings or (third_addon and len(postings) != order.refund_records.count())
                            or (not third_addon and len(postings) > (2 if existing_team or postmain_purchase or followup_addon
                                                        or paid_followup_addon else 1))):
                        raise serializers.ValidationError('主单退款凭证缺失')
                    if not third_addon:
                        verify_termination_locked(postings[0], allow_verified_later_final=len(postings) == 2)
                    if len(postings) == 2 and not third_addon:
                        if followup_addon:
                            from .points_transfer_followup_termination import verify_closed_or_settled_locked
                            verify_closed_or_settled_locked(postings[1])
                        else:
                            from .points_followup_termination import verify_followup_termination_locked
                            verify_followup_termination_locked(postings[1])
            else:
                from .ordinary_transfer_capability import permits as transferred_permits
                transferred_addon = transferred_permits(order, allow_addon_refund=True,
                                                        allow_addon_handover=True)
                if transferred_addon:
                    eligible_state = (order.status == Order.Status.IN_SERVICE or
                        (order.status == Order.Status.COMPLETED and deadline and timezone.now() < deadline))
                if transferred_addon and payment and eligible_state and not order.settled_at:
                    from .addon_handover import assert_no_pending_handover, current_responsibilities
                    from .addon_payment import verify_receipt
                    verify_receipt(payment)
                    pending = payment.handovers.select_for_update().filter(status='PENDING').first()
                    if pending:
                        from .ordinary_pending_handover_refund import permits as pending_refund_permits
                        if not pending_refund_permits(order, payment, pending):
                            assert_no_pending_handover(payment)
                    _, responsibilities = current_responsibilities(payment, lock=True)
                    candidates = {row['provider_id'] for row in responsibilities if row['basis'] > 0}
                    addon_only = bool(set(scope).issubset(candidates)
                                      and sum(row.amount - row.refunded_amount for row in payment.allocations.all()) > 0)
                    if not addon_only:
                        raise serializers.ValidationError('所选陪玩没有可退款的加购履约份额')
                else:
                    from .negotiation_zero import marked_execution_id
                    zero_addon = bool(marked_execution_id(order) and ordinary_addon_eligible(order))
                    zero_scope = False
                    if zero_addon and payment:
                        from .ordinary_zero_post_addon import continuing_account_locked
                        from .addon_handover import current_responsibilities
                        continuing = continuing_account_locked(order)
                        _, actual_shares = current_responsibilities(payment, lock=True)
                        zero_scope = (scope == [continuing] and any(
                            row['provider_id'] == continuing and row['basis'] > 0 for row in actual_shares))
                    pre_main_addon = (not zero_addon and order.refund_records.count() == 0
                                      and order.addon_intents.count() >= 2)
                    addon_only = bool(payment and eligible_state and not order.settled_at
                        and not order.providers.filter(settled_at__isnull=False).exists()
                        and (not payment.refund_records.exists() or zero_addon or order.addon_intents.count() >= 2)
                        and (zero_scope if zero_addon else scope == [payment.intent.provider_id])
                        and (pre_main_addon or zero_addon or order.refund_records.count() == 1)
                        and ordinary_addon_eligible(order))
                    if not addon_only:
                        raise serializers.ValidationError('当前加购售后范围不可用')
                    from .addon_payment import verify_receipt
                    from .negotiation_execution import verify_execution
                    from .negotiation_termination import verify_termination_evidence
                    verify_receipt(payment)
                    if zero_addon:
                        from .ordinary_zero_post_addon import continuing_account_locked
                        if scope != [continuing_account_locked(order)]:
                            raise serializers.ValidationError('加购售后只能选择继续履约的陪玩')
                    elif not pre_main_addon:
                        receipt = NegotiationExecution.objects.select_for_update().get(refund__order=order)
                        shares = verify_execution(receipt)
                        verify_termination_evidence(receipt)
                        if (len(shares) != 1 or (shares[0].account_id != payment.intent.provider_id
                                and not order.providers.filter(provider_account_id=payment.intent.provider_id,
                                    released_at__isnull=True, settled_at__isnull=True).exists())):
                            raise serializers.ValidationError('加购负责人或主单协商凭证待核对')
        completed_transfer_case = False
        if order.status == Order.Status.COMPLETED and segments is not None and not order.settled_at:
            deadline = aftersale_deadline(order)
            completed_transfer_case = bool(deadline and timezone.now() < deadline)
        completed_points_continuation = False
        if order.status == Order.Status.COMPLETED and not addon_only and not order.settled_at:
            from .points_zero_followup import applies as points_zero_applies
            from .negotiation_termination import continuing_team
            deadline = aftersale_deadline(order)
            if points_zero_applies(order) and deadline and timezone.now() < deadline:
                completed_points_continuation = bool(continuing_team(order, verify=True, allow_completed=True))
        completed_ordinary_continuation = False
        if order.status == Order.Status.COMPLETED and not addon_only and not order.settled_at:
            from .ordinary_addon_followup_termination import endpoint_enabled
            from .negotiation_termination import continuing_team
            deadline = aftersale_deadline(order)
            active_unsettled = list(order.providers.filter(released_at__isnull=True, settled_at__isnull=True)
                                    .values_list('provider_account_id', flat=True))
            if (deadline and timezone.now() < deadline and scope == active_unsettled
                    and len(active_unsettled) == 1 and endpoint_enabled(order)):
                completed_ordinary_continuation = bool(continuing_team(order, verify=True, allow_completed=True))
        if ((order.status != Order.Status.IN_SERVICE and not addon_only and not completed_transfer_case
             and not completed_points_continuation and not completed_ordinary_continuation)
                or order.is_gift_snapshot or not order.customer_account_id):
            raise serializers.ValidationError('仅能为服务中或售后期内的服务订单登记协商售后')
        from .negotiation_zero import MARKER as ZERO_NEGOTIATION_MARKER
        has_zero_receipt = (isinstance(order.funding_terms, dict)
                            and ZERO_NEGOTIATION_MARKER in order.funding_terms)
        continuing_negotiation = order.payment_status != Order.PaymentStatus.PAID or has_zero_receipt
        if continuing_negotiation and not addon_only:
            # An earlier scoped negotiation may have refunded one teammate
            # while another is still fulfilling the same order.  Verify that
            # exact continuation before reopening the shared case; ordinary
            # refunded orders must not gain a new in-service entry point.
            from .negotiation_termination import continuing_team
            if (order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED,
                                              Order.PaymentStatus.REFUNDED)
                    or (segments is None and not continuing_team(order, verify=True,
                         allow_completed=(completed_points_continuation or completed_ordinary_continuation)))):
                raise serializers.ValidationError('订单退款后的继续履约凭证待核对')
        active = list(order.providers.filter(**({} if addon_only else {'released_at__isnull': True})).values_list('provider_id', 'provider_account_id'))
        if not active and order.provider_id and not order.providers.exists():
            active = [(order.provider_id, order.provider_account_id)]
        if not active or any(not user_id or not account_id for user_id, account_id in active):
            raise serializers.ValidationError('当前陪玩身份待核对，请刷新订单')
        active_accounts = {account_id for _, account_id in active}
        historical_accounts = set(scope) - active_accounts
        if historical_accounts:
            # A transferred points order can affect an earlier participant's
            # retained, actually performed share as well as the current one.
            # This branch only opens the case; the normal proposal and refund
            # paths still reconcile every payment and transfer before posting.
            from .points_service_payment import needs_integrated_processing
            from .points_transfer_refund_preview import agreement_available_for_order
            from .negotiation_transfer_preview import agreement_testing_available
            points_agreement = needs_integrated_processing(order) and agreement_available_for_order(order)
            ordinary_test = not needs_integrated_processing(order) and agreement_testing_available(order)
            # A transferred provider may still be responsible for unpaid
            # add-on work even before its handover has been confirmed.
            from .addon_refund_execution import enabled as addon_refunds_enabled
            addon_accounts = set()
            if addon_refunds_enabled():
                from .addon_handover import current_responsibilities
                from .models import OrderAddonPayment, OrderParticipationSegment
                eligible_segments = set(OrderParticipationSegment.objects.filter(
                    attribution__order=order, provider_account_id__in=historical_accounts,
                    started_at__isnull=False, released_at__isnull=False)
                    .values_list('pk', flat=True))
                for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order):
                    _, shares = current_responsibilities(payment, lock=True)
                    addon_accounts.update(row['provider_id'] for row in shares
                                          if row['segment_id'] in eligible_segments and row['basis'] > 0)
            if (segments is None and (continuing_negotiation or (not (points_agreement or ordinary_test)
                                                                  and historical_accounts - addon_accounts))):
                raise serializers.ValidationError('所选陪玩不是当前履约人员，请刷新订单')
            from .models import OrderParticipationSegment
            past = set(OrderParticipationSegment.objects.filter(
                attribution__order=order, provider_account_id__in=historical_accounts,
                started_at__isnull=False, released_at__isnull=False, original_share__gt=0,
                original_transfer_out__isnull=False).values_list('provider_account_id', flat=True))
            past.update(addon_accounts)
            assignments = list(order.providers.filter(provider_account_id__in=historical_accounts,
                released_at__isnull=False, settled_at__isnull=True).values_list('provider_id', 'provider_account_id'))
            if past != historical_accounts or {account for _, account in assignments} != historical_accounts:
                raise serializers.ValidationError('所选历史陪玩履约或结算记录待核对')
            from club_accounts.models import LegacyAccountMap
            identities = set(LegacyAccountMap.objects.filter(account_id__in=historical_accounts)
                             .values_list('legacy_user_id', 'account_id'))
            if not set(assignments).issubset(identities):
                raise serializers.ValidationError('所选历史陪玩账号归属待核对')
            active.extend(assignments)
        from users.models import EscortProfile
        profiles = list(EscortProfile.objects.select_for_update().filter(
            account_id__in=[account for _, account in active]).order_by('pk').values_list('user_id', 'account_id'))
        if set(profiles) != set(active):
            raise serializers.ValidationError('当前陪玩账号归属待核对，请刷新订单')
        if case:
            if not case.history:
                append_history(case)
            case.reason, case.resolution, case.status = reason, '', 'OPEN'
            case.resolved_at, case.created_at = None, timezone.now()
            case.account_id = order.customer_account_id
        else:
            case = AfterSale(order=order, account_id=order.customer_account_id, reason=reason)
            case.created_at = timezone.now()
        case.operator = request.account
        case.client_request_id = request_id
        case.affected_provider_account_ids = scope
        case.affected_segment_ids = segments
        case.addon_payment_id = addon_payment_id if addon_only else None
        case.save()
        append_history(case)
        if consultation:
            case.history[-1]['consultation_message_id'] = consultation['message_id']
            case.history[-1]['consultation_session_id'] = consultation['session_id']
            case.history[-1]['consultation_account_id'] = consultation['account_id']
            case.history[-1]['consultation_business_cycle'] = consultation['business_cycle']
        case.save(update_fields=['history'])
        transaction.on_commit(lambda: notify_order_update(order))
        transaction.on_commit(lambda: notify_aftersale_reports(order))
        schedule_aftersale_notices(order, case, resolved=False, notify_customer=True)
        from .negotiation_followup import followup_enabled
        from .negotiation_proposals import enabled as proposal_enabled
        if continuing_negotiation:
            message = ('售后申请已登记，请提交方案并由双方确认' if followup_enabled() and proposal_enabled()
                       else '售后已登记，追加协商方案和退款暂未接通，请人工跟进')
        else:
            message = '售后申请已登记，双方确认及退款尚需后续处理'
        return Response({'code': 0, 'data': self.get_serializer(case).data,
                         'msg': message})

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def resolve(self, request, pk=None):
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        initial = self.get_object()
        order = Order.objects.select_for_update().get(pk=initial.order_id)
        row = AfterSale.objects.select_for_update().get(pk=initial.pk)
        if row.status != 'OPEN':
            raise serializers.ValidationError('该售后已处理')
        if row.addon_payment_id is not None and row.addon_payment.refund_proposals.filter(
                case=row, case_opened_at=row.created_at, case_request_id=row.client_request_id,
                status__in=('PENDING', 'ACCEPTED'), refund__isnull=True).exists():
            raise serializers.ValidationError('加购退款方案尚未处理，不能结束售后')
        from .negotiation_proposals import assert_case_can_resolve
        assert_case_can_resolve(row)
        row.resolution = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('resolution'))
        # Gift payment takes this mutex before its payment clock and holds it
        # until commit. Serialize normal closure against those payments before
        # recording the closure's pre-commit lower bound; a gift whose clock
        # is earlier must then already have committed.
        from wallet.refund_deposit import enabled as refund_deposit_enabled
        priority_guarded = False
        if refund_deposit_enabled():
            from .financial_lock import lock_order_money
            from users.models import EscortProfile
            lock_order_money()
            participants = list(order.providers.values_list('provider_id', 'provider_account_id'))
            participants.append((order.provider_id, order.provider_account_id))
            users = {user_id for user_id, _ in participants if user_id}
            accounts = {account_id for _, account_id in participants if account_id}
            profiles = list(EscortProfile.objects.select_for_update().filter(
                Q(user_id__in=users) | Q(account_id__in=accounts)).order_by('account_id', 'pk'))
            priority_guarded = bool(profiles)
        row.status = 'RESOLVED'
        row.operator = request.account
        row.resolved_at = timezone.now()
        append_history(row)
        if priority_guarded and order.is_gift_snapshot:
            row.history[-1]['gift_income_priority_guard'] = 1
        if priority_guarded:
            from wallet.income_priority_evidence import record_closure
            evidence = record_closure(row)
            if evidence:
                row.history[-1]['income_priority_closure_id'] = evidence.pk
        row.save()
        transaction.on_commit(lambda: notify_order_update(order))
        transaction.on_commit(lambda: notify_aftersale_reports(order))
        schedule_aftersale_notices(order, row, resolved=True)
        return Response({'code': 0, 'data': self.get_serializer(row).data})

    @action(detail=True, methods=['post'])
    @protect_gift_refund('case')
    @transaction.atomic
    def refund(self, request, pk=None):
        from .state_machine import IllegalTransitionError
        from .settlement import SettlementError
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        initial = self.get_object()
        order = Order.objects.select_for_update().get(pk=initial.order_id)
        row = AfterSale.objects.select_for_update().get(pk=initial.pk)
        if row.addon_payment_id is not None:
            raise serializers.ValidationError('该售后仅适用于指定加购，不能退款主单')
        from .refunds import replay_refund,apply_admin_refund
        resolution = serializers.CharField(min_length=2, max_length=1000).run_validation(request.data.get('resolution'))
        try:
            existing = replay_refund(order,request.data.get('client_request_id'),request.data.get('refund_amount'),resolution)
        except RefundError as exc:
            return Response({'code':409,'errorCode':exc.code,'msg':str(exc),'data':exc.data})
        if existing:
            return Response({'code':0,'data':self.get_serializer(row).data})
        if row.status != 'OPEN':
            raise serializers.ValidationError('该售后已处理')
        try:
            order = apply_admin_refund(order.pk,reason=resolution,operator=request.legacy_user,operator_account=request.account,
                plan_token=request.data.get('plan_token'),refund_amount=request.data.get('refund_amount'),
                client_request_id=request.data.get('client_request_id'),notify=False)
        except RefundError as exc:
            return Response({'code':409 if exc.code in ('REFUND_PLAN_CHANGED','REFUND_REQUEST_CONFLICT') else 400,
                             'errorCode':exc.code,'msg':str(exc),'data':exc.data})
        except (IllegalTransitionError, SettlementError, WalletAddressingError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        row.status = 'REFUNDED' if order.payment_status == 'REFUNDED' else 'PARTIALLY_REFUNDED'
        row.resolution = resolution
        row.operator = request.account
        row.resolved_at = timezone.now()
        append_history(row)
        from wallet.income_priority_evidence import attach_closure
        attach_closure(row)
        row.save()
        transaction.on_commit(lambda: notify_order_update(order))
        return Response({'code': 0, 'data': self.get_serializer(row).data})
