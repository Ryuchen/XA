from datetime import timedelta
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from users.models import EscortProfile
from .models import Order, OrderApplication, OrderProvider
from .notice_text import service_arrangement_hint
from .services import count_active_orders, lock_provider_dispatch, mark_escort_busy, sync_single_provider_share, push_message_safe
from .state_machine import transition, IllegalTransitionError
from .notifier import notify_order_update
from .team_rules import required_team_size, team_size_error

class ApplicationError(ValueError):
    pass

def public_order(order):
    from .direct_payment_models import DirectPaymentIntent
    from .direct_payment_quote import quote_matches_intent
    intent = DirectPaymentIntent.objects.filter(order=order).first()
    if intent and (intent.status != DirectPaymentIntent.Status.PAID or not quote_matches_intent(intent, order)):
        raise ApplicationError('支付证据待核对，订单暂停报名')
    if (order.status != Order.Status.PENDING or order.dispatch_route != Order.DispatchRoute.PUBLIC or not order.requires_boss_selection
        or order.provider_id or order.provider_account_id or order.source_order_id
        or order.payment_status != Order.PaymentStatus.PAID
        or order.is_gift_snapshot
        or (order.auto_cancel_at and order.auto_cancel_at <= timezone.now())):
        raise ApplicationError('订单已结束报名，请刷新订单')

def eligible(order, account, *, visibility=True, profile=None, active_count=None, club_rules_enabled=None, visibility_delay=None):
    if profile is None:
        profile = EscortProfile.objects.select_related('account','user','level').prefetch_related('qualifications__game_category','service_items__game_category').filter(account=account).first()
    if account.account_type != ClubAccount.AccountType.PROVIDER or not profile:
        raise ApplicationError('陪玩资料不存在')
    reason = profile.order_acceptance_error(order, provider_gender=order.provider_gender, club_rules_enabled=club_rules_enabled)
    if reason:
        raise ApplicationError(reason)
    if (count_active_orders(account) if active_count is None else active_count) >= Order.MAX_CONCURRENT_ORDERS:
        raise ApplicationError('陪玩接单名额已满，请重新选择')
    delay = profile.order_visibility_delay_seconds if visibility and visibility_delay is None else visibility_delay
    if visibility and timezone.now() < (order.pool_entered_at or order.created_at) + timedelta(seconds=delay):
        raise ApplicationError('当前通行证尚未到抢单时间')
    return profile

def member_data(account,order=None, *, profiles=None):
    profile = profiles.get(account.pk) if profiles is not None else getattr(account, 'escort_profile', None)
    level = profile.level if profile else None
    from users.qualifications import service_requirement,matching_qualification,grade_options,GRADES
    requirement=service_requirement(order) if order else None
    qualification=matching_qualification(profile,requirement) if profile and requirement else None
    grade_name=GRADES[qualification.grade] if qualification else (level.name if level and level.is_active else '未定级')
    rank=grade_options(qualification.provider_kind,qualification.platform,qualification.security_level).index(qualification.grade) if qualification else (profile.level_rank if profile else -1)
    return {'id': account.pk, 'nickname': profile.display_name if profile else account.nickname,
            'level': grade_name,
            'level_rank': rank,
            'pass_tier': profile.active_pass_tier if profile else '',
            'avatar_url': account.avatar_url or ''}

def application_data(row,order=None, *, profiles=None):
    members = [member_data(row.applicant,order,profiles=profiles)] + ([member_data(row.partner,order,profiles=profiles)] if row.partner_id else [])
    return {'id': row.pk, 'team_size': len(members), 'members': members,
            'partner_confirmed': row.partner_confirmed, 'status': row.status,
            'ready': row.status == 'PENDING' and (not row.partner_id or row.partner_confirmed),
            'created_at': row.created_at}

def participant_acceptance_reason(order, row, account):
    """Only disclose the requesting participant's own acceptance reason."""
    members = [row.applicant] + ([row.partner] if row.partner_id else [])
    members.sort(key=lambda member: member.pk != account.pk)
    for member in members:
        try:
            eligible(order, member)
        except ApplicationError as exc:
            return (str(exc) if member.pk == account.pk else
                    '队友当前暂不符合接单条件，请与队友确认或联系客服。')
    return ''

def application_rows(order):
    return order.application_list if hasattr(order,'application_list') else order.applications.select_related('applicant__escort_profile__level', 'partner__escort_profile__level')

def applications_data(order, request, *, validate_readiness=False):
    if not request or not getattr(request, 'account', None):
        return []
    account = request.account
    if account.pk != order.customer_account_id:
        return []
    applications = [row for row in application_rows(order) if row.status == 'PENDING']
    readiness = {}
    profiles = None
    if validate_readiness and applications:
        from .services import ACTIVE_ORDER_STATUSES
        from wallet.club_rules import club_income_rules_enabled
        from wallet.models import get_pass_delay_seconds
        accounts = {member.pk: member for row in applications
                    for member in ([row.applicant] + ([row.partner] if row.partner_id else []))}
        profiles = {profile.account_id: profile for profile in EscortProfile.objects.filter(account_id__in=accounts)
                    .select_related('account', 'user', 'level')
                    .prefetch_related('qualifications__game_category', 'service_items__game_category', 'user__schedules')}
        active = {account_id: set() for account_id in accounts}
        for account_id, order_id in Order.objects.filter(provider_account_id__in=accounts, status__in=ACTIVE_ORDER_STATUSES).values_list('provider_account_id', 'pk'):
            active[account_id].add(order_id)
        for account_id, order_id in OrderProvider.objects.filter(provider_account_id__in=accounts,
                order__status__in=ACTIVE_ORDER_STATUSES, released_at__isnull=True,
                settled_at__isnull=True).values_list('provider_account_id', 'order_id'):
            active[account_id].add(order_id)
        rules_enabled = club_income_rules_enabled()
        tiers = {account_id: profile.active_pass_tier for account_id, profile in profiles.items()}
        delays = {tier: get_pass_delay_seconds(tier) for tier in set(tiers.values())}
        for account_id, member in accounts.items():
            profile = profiles.get(account_id)
            if profile is None:
                readiness[account_id] = '陪玩资料不存在'
                continue
            try:
                eligible(order, member, profile=profile, active_count=len(active[account_id]),
                         club_rules_enabled=rules_enabled, visibility_delay=delays[tiers[account_id]])
                readiness[account_id] = ''
            except ApplicationError as exc:
                readiness[account_id] = str(exc)
    rows = []
    for application in applications:
        data = application_data(application, order, profiles=profiles)
        if validate_readiness and data['ready']:
            members = [application.applicant] + ([application.partner] if application.partner_id else [])
            for member, member_details in zip(members, data['members']):
                if readiness[member.pk]:
                    data['ready'] = False
                    data['not_ready_reason'] = f"{member_details['nickname'] or '陪玩'}：{readiness[member.pk]}"
                    break
        rows.append(data)
    # 组队先比较最低等级，再比较最高等级；一人按本人等级。相同等级保持报名顺序。
    rows.sort(key=lambda row: (-min(m['level_rank'] for m in row['members']), -max(m['level_rank'] for m in row['members']), row['id']))
    return rows

def my_applications_data(order, request):
    if not request or not getattr(request, 'account', None) or request.account.account_type != 'PROVIDER':
        return []
    # The order overview shows each applicant's current round. Historical
    # invitations remain available by their own immutable application ID.
    latest = {}
    for row in application_rows(order):
        previous = latest.get(row.applicant_id)
        if previous is None or (row.created_at, row.pk) > (previous.created_at, previous.pk):
            latest[row.applicant_id] = row
    rows = [row for row in latest.values() if request.account.pk in (row.applicant_id, row.partner_id)]
    rows.sort(key=lambda row: (row.status != 'PENDING', row.created_at, row.pk))
    return [dict(application_data(row,order), is_partner=row.partner_id == request.account.pk) for row in rows]

def response_order(order, request, msg):
    from .serializers import OrderSerializer
    return Response({'code': 0, 'msg': msg, 'data': OrderSerializer(order, context={
        'request': request, '_provider_pool_view': request.account.account_type == 'PROVIDER',
    }).data})


def notify_cancelled_applicants(order):
    """Notify each pending applicant once, with a link to their own record."""
    from club_accounts.models import LegacyAccountMap
    applications = list(order.applications.filter(status='PENDING').values_list('pk', 'applicant_id', 'partner_id'))
    recipients = {}
    for application_id, applicant_id, partner_id in applications:
        for account_id in (applicant_id, partner_id):
            if account_id:
                recipients.setdefault(account_id, application_id)
    legacy = dict(LegacyAccountMap.objects.filter(account_id__in=recipients).values_list('account_id', 'legacy_user_id'))
    for account_id, application_id in recipients.items():
        user_id = legacy.get(account_id)
        if user_id:
            transaction.on_commit(lambda user_id=user_id, account_id=account_id, application_id=application_id:
                push_message_safe(recipient_id=user_id, recipient_account_id=account_id, title='报名订单已取消',
                    preview=f'订单「{order.order_no}」已取消，本次报名结束。', msg_type='ORDER',
                    related_order_id=order.pk, action_url=f'/pages/orders/index?application_id={application_id}'))


def notify_unselected_applicants(order, applications, selected_accounts):
    """Let the other applicants revisit their own outcome after selection."""
    from club_accounts.models import LegacyAccountMap
    recipients = {}
    for application_id, applicant_id, partner_id in applications:
        for account_id in (applicant_id, partner_id):
            if account_id and account_id not in selected_accounts:
                recipients.setdefault(account_id, application_id)
    legacy = dict(LegacyAccountMap.objects.filter(account_id__in=recipients).values_list('account_id', 'legacy_user_id'))
    for account_id, application_id in recipients.items():
        user_id = legacy.get(account_id)
        if user_id:
            transaction.on_commit(lambda user_id=user_id, account_id=account_id, application_id=application_id:
                push_message_safe(recipient_id=user_id, recipient_account_id=account_id, title='报名结果已更新',
                    preview=f'订单「{order.order_no}」已选择其他陪玩，本次报名结束。', msg_type='ORDER',
                    related_order_id=order.pk, action_url=f'/pages/orders/index?application_id={application_id}'))


def close_applications_for_staff_assignment(order, selected_accounts):
    """Close the current public application round while the order is locked."""
    selected_accounts = set(selected_accounts)
    applications = list(order.applications.filter(status='PENDING').values_list('pk', 'applicant_id', 'partner_id'))
    if not applications:
        return
    matched = [pk for pk, applicant_id, partner_id in applications
               if {account_id for account_id in (applicant_id, partner_id) if account_id} == selected_accounts]
    pending = order.applications.filter(status='PENDING')
    pending.exclude(pk__in=matched).update(status='NOT_SELECTED', updated_at=timezone.now())
    pending.filter(pk__in=matched).update(status='SELECTED', updated_at=timezone.now())
    notify_unselected_applicants(order, applications, selected_accounts)


class MyApplicationDetailView(APIView):
    """Let a member revisit an existing invitation even while absent from the pool."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id, application_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看报名'}, status=403)
        row = (OrderApplication.objects.select_related('order', 'applicant', 'partner')
               .filter(order_id=order_id, pk=application_id, status__in=['PENDING', 'SELECTED', 'NOT_SELECTED', 'WITHDRAWN'])
               .filter(Q(applicant=request.account) | Q(partner=request.account))
               .order_by('-pk').first())
        if row is None:
            return Response({'code': 404, 'msg': '报名不存在或已处理'}, status=404)
        order = row.order
        if row.status == 'WITHDRAWN':
            from .serializers import OrderSerializer
            return Response({'code': 0, 'data': {
                'order': OrderSerializer(order, context={'request': request}).data,
                'application': dict(application_data(row, order), is_partner=row.partner_id == request.account.pk),
                'can_confirm': False, 'confirm_reason': '', 'closed_reason': '报名已撤回。',
            }})
        if row.status == 'SELECTED':
            from .serializers import OrderSerializer
            if order.status == Order.Status.PENDING:
                closed_reason = '本次报名曾被选中，订单已回到待接单池。'
            elif order.status == Order.Status.CANCELLED:
                closed_reason = '本次报名曾被选中，订单已取消。'
            elif order.applications.filter(status='SELECTED').order_by('-pk').values_list('pk', flat=True).first() != row.pk:
                closed_reason = '本次报名曾被选中，订单已有新的服务安排。'
            else:
                # A staff transfer changes the active team without creating a
                # new selection. Keep the old application but describe the
                # current arrangement using its actual participants.
                from club_accounts.models import LegacyAccountMap
                active = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', 'provider_id'))
                if order.provider_account_id or order.provider_id:
                    active.append((order.provider_account_id, order.provider_id))
                missing = {legacy_id for account_id, legacy_id in active if not account_id and legacy_id}
                legacy_accounts = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=missing).values_list('legacy_user_id', 'account_id'))
                accounts = [account_id or legacy_accounts.get(legacy_id) for account_id, legacy_id in active]
                selected = {row.applicant_id} | ({row.partner_id} if row.partner_id else set())
                changed = bool(accounts) and all(accounts) and set(accounts) != selected
                closed_reason = ('本次报名曾被选中，当前服务人员已调整。' if changed
                                 else '本次报名已选中，请查看订单状态。')
            return Response({'code': 0, 'data': {
                'order': OrderSerializer(order, context={'request': request}).data,
                'application': dict(application_data(row, order), is_partner=row.partner_id == request.account.pk),
                'can_confirm': False, 'confirm_reason': '', 'closed_reason': closed_reason,
            }})
        if row.status == 'NOT_SELECTED':
            from .serializers import OrderSerializer
            closed_reason = ('本次报名未被选中，订单现为待接单状态。' if order.status == Order.Status.PENDING else
                             '本次报名未被选中，订单现已取消。' if order.status == Order.Status.CANCELLED else
                             '订单已选择其他陪玩，本次报名结束。')
            return Response({'code': 0, 'data': {
                'order': OrderSerializer(order, context={'request': request}).data,
                'application': dict(application_data(row, order), is_partner=row.partner_id == request.account.pk),
                'can_confirm': False, 'confirm_reason': '', 'closed_reason': closed_reason,
            }})
        if order.status == Order.Status.CANCELLED:
            from .serializers import OrderSerializer
            return Response({'code': 0, 'data': {
                'order': OrderSerializer(order, context={'request': request}).data,
                'application': dict(application_data(row, order), is_partner=row.partner_id == request.account.pk),
                'can_confirm': False, 'confirm_reason': '', 'closed_reason': '订单已取消，本次报名结束。',
            }})
        try:
            public_order(order)
        except ApplicationError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        confirm_reason = ''
        if row.partner_id != request.account.pk:
            confirm_reason = '等待队友确认' if row.partner_id and not row.partner_confirmed else ''
        elif not row.partner_confirmed:
            confirm_reason = team_size_error(order, 2) or participant_acceptance_reason(order, row, request.account)
        # Confirmation and current selection eligibility are separate facts.
        # Another member's private acceptance reason is not returned here.
        selectable = None
        selection_reason = ''
        if not row.partner_id or row.partner_confirmed:
            selection_reason = (team_size_error(order, 2 if row.partner_id else 1) or
                                participant_acceptance_reason(order, row, request.account))
            selectable = not selection_reason
        from .serializers import OrderSerializer
        return Response({'code': 0, 'data': {
            'order': OrderSerializer(order, context={'request': request, '_provider_pool_view': True}).data,
            'application': dict(application_data(row, order), is_partner=row.partner_id == request.account.pk),
            'can_confirm': bool(row.partner_id == request.account.pk and not row.partner_confirmed and not confirm_reason),
            'confirm_reason': confirm_reason, 'selectable': selectable, 'selection_reason': selection_reason,
        }})


class MyApplicationListView(APIView):
    """Resolve older order-only notices without guessing between invitations."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看报名'}, status=403)
        rows = list(OrderApplication.objects.select_related('order', 'applicant', 'partner')
                    .filter(order_id=order_id, status='PENDING')
                    .filter(Q(applicant=request.account) | Q(partner=request.account)).order_by('pk'))
        if rows:
            try:
                public_order(rows[0].order)
            except ApplicationError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
        return Response({'code': 0, 'data': {'applications': [
            dict(application_data(row, row.order), is_partner=row.partner_id == request.account.pk)
            for row in rows
        ]}})

class ApplyOrderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    def post(self, request, order_id):
        if request.account.account_type != 'PROVIDER':
            return Response({'code': 403, 'msg': '仅陪玩可报名抢单'})
        size = request.data.get('team_size', 1)
        partner_id = request.data.get('partner_id')
        if type(size) is not int or size not in (1,2) or (size == 1 and partner_id is not None) or (size == 2 and (type(partner_id) is not int or partner_id == request.account.pk)):
            return Response({'code': 400, 'msg': '请选择一人报名，或两人报名并指定另一名陪玩'})
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_id)
                public_order(order)
                error = team_size_error(order, size)
                if error:
                    raise ApplicationError(error)
                partner = None
                if size == 2:
                    partner = ClubAccount.objects.get(pk=partner_id, account_type='PROVIDER')
                # A non-forced ban uses the same provider locks, so the
                # account state and the newly created application cannot
                # cross a concurrent ban using a stale request.account.
                lock_provider_dispatch([request.account.pk] + ([partner.pk] if partner else []))
                eligible(order, request.account)
                partner_profile = None
                if partner:
                    partner_profile = eligible(order, partner)
                row = OrderApplication.objects.filter(order=order, applicant=request.account, status='PENDING').first()
                if row:
                    if row.partner_id != partner_id:
                        raise ApplicationError('已有报名，请先撤回后修改人数或队友')
                else:
                    row = OrderApplication.objects.create(order=order, applicant=request.account,
                        partner=partner, partner_confirmed=size == 1, status='PENDING')
                    if partner_profile:
                        transaction.on_commit(lambda user_id=partner_profile.user_id, account_id=partner.pk, current=order: push_message_safe(
                            recipient_id=user_id, recipient_account_id=account_id, title='收到组队邀请',
                            preview=f'有陪玩邀请你一起报名「{current.service_name_snapshot}」，请及时确认',
                            msg_type='ORDER', action_url=f'/pages/orders/index?application_id={row.pk}', related_order_id=current.pk))
                transaction.on_commit(lambda: notify_order_update(order))
        except (Order.DoesNotExist, ClubAccount.DoesNotExist):
            return Response({'code': 404, 'msg': '订单或队友不存在'})
        except ApplicationError as exc:
            return Response({'code': 400, 'msg': str(exc)})
        return response_order(order, request, '报名成功，等待队友确认' if size == 2 else '报名成功，等待老板选择')

class ApplicationActionView(APIView):
    throttle_scope = 'order_write'
    permission_classes = [IsClubAccountAuthenticated]
    def post(self, request, order_id, application_id):
        action = request.data.get('action')
        if action not in ('CONFIRM_TEAM','WITHDRAW'):
            return Response({'code': 400, 'msg': '不支持的报名操作'})
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_id)
                public_order(order)
                row = OrderApplication.objects.get(pk=application_id, order=order)
                if request.account.pk not in (row.applicant_id,row.partner_id) or request.account.account_type != 'PROVIDER':
                    return Response({'code': 403, 'msg': '只能处理自己的抢单报名'})
                if row.status != 'PENDING':
                    raise ApplicationError('该报名已处理')
                if action == 'CONFIRM_TEAM':
                    if row.partner_id != request.account.pk:
                        raise ApplicationError('请由队友本人确认')
                    error = team_size_error(order, 2)
                    if error:
                        raise ApplicationError(error)
                    lock_provider_dispatch([row.applicant_id, row.partner_id])
                    error = participant_acceptance_reason(order, row, request.account)
                    if error:
                        raise ApplicationError(error)
                    applicant_profile = EscortProfile.objects.get(account=row.applicant)
                    newly_confirmed = not row.partner_confirmed
                    row.partner_confirmed = True
                    if newly_confirmed:
                        transaction.on_commit(lambda user_id=applicant_profile.user_id, account_id=row.applicant_id, current=order: push_message_safe(
                            recipient_id=user_id, recipient_account_id=account_id, title='队友已确认组队',
                            preview=f'「{current.service_name_snapshot}」组队报名已确认，等待老板选择',
                            msg_type='ORDER', action_url=f'/pages/orders/index?application_id={row.pk}', related_order_id=current.pk))
                else:
                    row.status = 'WITHDRAWN'
                    if row.partner_id:
                        other_id = row.partner_id if request.account.pk == row.applicant_id else row.applicant_id
                        other_user_id = EscortProfile.objects.filter(account_id=other_id).values_list('user_id', flat=True).first()
                        if other_user_id:
                            declined = request.account.pk == row.partner_id and not row.partner_confirmed
                            title = '队友已拒绝组队邀请' if declined else '队友已撤回组队报名'
                            preview = (f'订单 {order.order_no} 的组队邀请已被拒绝，可重新报名或选择其他订单'
                                       if declined else f'订单 {order.order_no} 的组队报名已撤回，可重新报名或选择其他订单')
                            transaction.on_commit(lambda user_id=other_user_id, account_id=other_id, current=order, title=title, preview=preview: push_message_safe(
                                recipient_id=user_id, recipient_account_id=account_id, title=title,
                                preview=preview,
                                msg_type='ORDER', related_order_id=current.pk,
                                action_url='/pages/orders/index?tab=pool'))
                row.save(update_fields=['partner_confirmed','status','updated_at'])
                transaction.on_commit(lambda: notify_order_update(order))
        except (Order.DoesNotExist, OrderApplication.DoesNotExist):
            return Response({'code': 404, 'msg': '报名不存在'})
        except ApplicationError as exc:
            return Response({'code': 400, 'msg': str(exc)})
        return response_order(order, request, '队伍已确认，等待老板选择' if action == 'CONFIRM_TEAM' else '报名已撤回')

class SelectApplicationView(APIView):
    throttle_scope = 'order_write'
    permission_classes = [IsClubAccountAuthenticated]
    def post(self, request, order_id, application_id):
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_id)
                if request.account.account_type != 'BOSS' or order.customer_account_id != request.account.pk:
                    return Response({'code': 403, 'msg': '仅下单老板可以选择陪玩'})
                row = OrderApplication.objects.get(pk=application_id, order=order)
                if row.status == 'SELECTED' and order.status in ('GRABBED','IN_SERVICE','COMPLETED'):
                    # Earlier rounds remain SELECTED for history. A replay is
                    # successful only for the selection currently on the order.
                    latest = order.applications.filter(status='SELECTED').order_by('-pk').values_list('pk', flat=True).first()
                    grab = order.status_logs.filter(to_status='GRABBED').order_by('-pk').first()
                    participants = set(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
                    participants.discard(None)
                    if order.provider_account_id:
                        participants.add(order.provider_account_id)
                    selected = {row.applicant_id} | ({row.partner_id} if row.partner_id else set())
                    boss_grab = grab and grab.action == 'GRAB' and (
                        grab.operator_account_id == order.customer_account_id if grab.operator_account_id
                        else bool(grab.operator_id and grab.operator_id == order.customer_id)
                    )
                    if latest == row.pk and boss_grab and participants == selected:
                        return response_order(order, request, '已选择该陪玩')
                    raise ApplicationError('报名或订单状态已变化，请刷新后核对')
                public_order(order)
                if row.status != 'PENDING' or (row.partner_id and not row.partner_confirmed):
                    raise ApplicationError('报名已撤回或队友尚未确认')
                accounts = [row.applicant] + ([row.partner] if row.partner_id else [])
                error = team_size_error(order, len(accounts))
                if error:
                    raise ApplicationError(error)
                lock_provider_dispatch([a.pk for a in accounts])
                profiles = [eligible(order,a) for a in accounts]
                unselected = list(order.applications.filter(status='PENDING').exclude(pk=row.pk)
                                  .values_list('pk', 'applicant_id', 'partner_id'))
                def assign(current):
                    current.provider = profiles[0].user
                    current.provider_account = accounts[0]
                    current.provider_name_snapshot = profiles[0].display_name
                    current.escort_mode = 'DOUBLE' if len(accounts) == 2 else 'SINGLE'
                    if len(accounts) == 1:
                        sync_single_provider_share(current)
                    else:
                        from .pricing import club_provider_specs
                        from .settlement import build_provider_shares, aggregate_order_split
                        specs = club_provider_specs(current, profiles, current.amount)
                        if specs is None:
                            specs = [{'commission_rate': current.commission_rate} for _ in profiles]
                        shares = build_provider_shares(current.amount,specs)
                        split = aggregate_order_split(current.amount,shares,current.inviter_commission_rate_snapshot if current.inviter_id else 0)
                        current.commission_rate = split.commission_rate
                        current.provider_income = split.provider_income
                        current.inviter_commission = split.inviter_commission
                        current.shop_income = split.shop_income
                        current.providers.filter(settled_at__isnull=True).update(released_at=timezone.now(), settlement_base=0, provider_income=0, commission_fixed=0)
                        for profile, share in zip(profiles,shares):
                            OrderProvider.objects.update_or_create(order=current,provider=profile.user,defaults={
                                'provider_account':profile.account,'provider_name_snapshot':profile.display_name,
                                'settlement_base':share.settlement_base,'commission_type':share.commission_type,
                                'commission_rate':share.commission_rate,'commission_fixed':share.commission_fixed,
                                'provider_income':share.provider_income,'released_at':None})
                    mark_escort_busy(account_ids=[a.pk for a in accounts])
                    current.applications.filter(status='PENDING').exclude(pk=row.pk).update(status='NOT_SELECTED')
                    row.status='SELECTED';row.save(update_fields=['status','updated_at'])
                order = transition(order.pk,Order.Status.GRABBED,operator=request.legacy_user,operator_account=request.account,
                    reason='老板选择抢单陪玩',side_effect=assign,
                    update_fields=['provider','provider_account','provider_name_snapshot','escort_mode','commission_rate','provider_income','inviter_commission','shop_income'])
                notify_unselected_applicants(order, unselected, {account.pk for account in accounts})
                transaction.on_commit(lambda: notify_order_update(order))
                service_hint = service_arrangement_hint(order)
                for profile, account in zip(profiles, accounts):
                    transaction.on_commit(lambda user_id=profile.user_id, account_id=account.pk, current=order, hint=service_hint: push_message_safe(
                        recipient_id=user_id, recipient_account_id=account_id, title='老板已选择你服务',
                        preview=f'你已被选中服务「{current.service_name_snapshot}」，{hint}',
                        msg_type='ORDER', related_order_id=current.pk, assignment_alert=True))
        except (Order.DoesNotExist, OrderApplication.DoesNotExist):
            return Response({'code': 404, 'msg': '订单或报名不存在'})
        except (ApplicationError, IllegalTransitionError) as exc:
            return Response({'code': 400, 'msg': str(exc)})
        return response_order(order, request, '已选择陪玩，请等待开始服务')

class TeammateListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    def get(self, request, order_id):
        if request.account.account_type != 'PROVIDER':
            return Response({'code':403,'msg':'仅陪玩可选择队友'})
        try:
            order=Order.objects.get(pk=order_id);public_order(order);eligible(order,request.account)
            if required_team_size(order) != 2:
                raise ApplicationError('单陪套餐不支持选择队友')
        except (Order.DoesNotExist, ApplicationError):
            return Response({'code':404,'msg':'订单不可报名'})
        from .services import active_orders_for
        from wallet.club_rules import club_income_rules_enabled
        from wallet.models import get_pass_delay_seconds
        # Reuse the same active-order predicate as the final acceptance check,
        # but calculate it for the whole roster rather than querying per person.
        active = (active_orders_for(account_id=OuterRef('account_id')).order_by()
                  .values(group=Value(1)).annotate(total=Count('pk', distinct=True)).values('total'))
        profiles = list(EscortProfile.objects.select_related('account', 'user', 'level')
            .prefetch_related('qualifications__game_category', 'service_items__game_category', 'user__schedules')
            .filter(account__account_type='PROVIDER', account__is_active=True, account__can_login=True, is_verified=True)
            .exclude(account=request.account)
            .annotate(picker_active_count=Coalesce(Subquery(active, output_field=IntegerField()), 0)))
        rules_enabled = club_income_rules_enabled()
        tiers = {profile.account_id: profile.active_pass_tier for profile in profiles}
        delays = {tier: get_pass_delay_seconds(tier) for tier in set(tiers.values())}
        members=[]
        for profile in profiles:
            try:
                eligible(order, profile.account, profile=profile, active_count=profile.picker_active_count,
                         club_rules_enabled=rules_enabled, visibility_delay=delays[tiers[profile.account_id]])
            except ApplicationError: continue
            members.append(member_data(profile.account, order, profiles={profile.account_id: profile}))
        members.sort(key=lambda item:(-item['level_rank'],item['id']))
        return Response({'code':0,'data':members})
