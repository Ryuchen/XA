from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from users.models import EscortProfile
from .models import Order, OrderApplication, OrderProvider
from .services import count_active_orders, lock_provider_dispatch, mark_escort_busy, sync_single_provider_share
from .state_machine import transition, IllegalTransitionError
from .notifier import notify_order_update
from .team_rules import required_team_size, team_size_error

class ApplicationError(ValueError):
    pass

def public_order(order):
    if (order.status != Order.Status.PENDING or not order.requires_boss_selection
        or order.provider_id or order.provider_account_id or order.source_order_id
        or order.payment_status != Order.PaymentStatus.PAID
        or (order.service.service_category and order.service.service_category.is_gift)
        or (order.auto_cancel_at and order.auto_cancel_at <= timezone.now())):
        raise ApplicationError('订单已结束报名，请刷新订单')

def eligible(order, account, *, visibility=True):
    profile = EscortProfile.objects.select_related('account','user','level').prefetch_related('qualifications__game_category','service_items__game_category').filter(account=account).first()
    if account.account_type != ClubAccount.AccountType.PROVIDER or not profile:
        raise ApplicationError('陪玩资料不存在')
    reason = profile.order_acceptance_error(order, provider_gender=order.provider_gender)
    if reason:
        raise ApplicationError(reason)
    if count_active_orders(account) >= Order.MAX_CONCURRENT_ORDERS:
        raise ApplicationError('陪玩接单名额已满，请重新选择')
    if visibility and timezone.now() < order.created_at + timedelta(seconds=profile.order_visibility_delay_seconds):
        raise ApplicationError('当前通行证尚未到抢单时间')
    return profile

def member_data(account,order=None):
    profile = getattr(account, 'escort_profile', None)
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

def application_data(row,order=None):
    members = [member_data(row.applicant,order)] + ([member_data(row.partner,order)] if row.partner_id else [])
    return {'id': row.pk, 'team_size': len(members), 'members': members,
            'partner_confirmed': row.partner_confirmed, 'status': row.status,
            'ready': row.status == 'PENDING' and (not row.partner_id or row.partner_confirmed),
            'created_at': row.created_at}

def application_rows(order):
    return order.application_list if hasattr(order,'application_list') else order.applications.select_related('applicant__escort_profile__level', 'partner__escort_profile__level')

def applications_data(order, request):
    if not request or not getattr(request, 'account', None):
        return []
    account = request.account
    if account.pk != order.customer_account_id:
        return []
    rows = [application_data(row,order) for row in application_rows(order) if row.status == 'PENDING']
    # 组队先比较最低等级，再比较最高等级；一人按本人等级。相同等级保持报名顺序。
    rows.sort(key=lambda row: (-min(m['level_rank'] for m in row['members']), -max(m['level_rank'] for m in row['members']), row['id']))
    return rows

def my_applications_data(order, request):
    if not request or not getattr(request, 'account', None) or request.account.account_type != 'PROVIDER':
        return []
    return [dict(application_data(row,order), is_partner=row.partner_id == request.account.pk)
            for row in application_rows(order) if request.account.pk in (row.applicant_id,row.partner_id)]

def response_order(order, request, msg):
    from .serializers import OrderSerializer
    return Response({'code': 0, 'msg': msg, 'data': OrderSerializer(order, context={'request': request}).data})

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
                eligible(order, request.account)
                partner = None
                if size == 2:
                    partner = ClubAccount.objects.get(pk=partner_id, account_type='PROVIDER')
                    eligible(order, partner)
                row = OrderApplication.objects.filter(order=order, applicant=request.account).first()
                if row and row.status == 'PENDING':
                    if row.partner_id != partner_id:
                        raise ApplicationError('已有报名，请先撤回后修改人数或队友')
                else:
                    OrderApplication.objects.update_or_create(order=order, applicant=request.account,
                        defaults={'partner': partner, 'partner_confirmed': size == 1, 'status': 'PENDING'})
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
                    eligible(order, row.applicant)
                    eligible(order, row.partner)
                    row.partner_confirmed = True
                else:
                    row.status = 'WITHDRAWN'
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
                    return response_order(order, request, '已选择该陪玩')
                public_order(order)
                if row.status != 'PENDING' or (row.partner_id and not row.partner_confirmed):
                    raise ApplicationError('报名已撤回或队友尚未确认')
                accounts = [row.applicant] + ([row.partner] if row.partner_id else [])
                error = team_size_error(order, len(accounts))
                if error:
                    raise ApplicationError(error)
                lock_provider_dispatch([a.pk for a in accounts])
                profiles = [eligible(order,a) for a in accounts]
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
                transaction.on_commit(lambda: notify_order_update(order))
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
        profiles=EscortProfile.objects.select_related('account','user','level').prefetch_related('qualifications__game_category','service_items__game_category').filter(account__account_type='PROVIDER',account__is_active=True,account__can_login=True,is_verified=True).exclude(account=request.account)
        members=[]
        for profile in profiles:
            try: eligible(order,profile.account)
            except ApplicationError: continue
            members.append(member_data(profile.account,order))
        members.sort(key=lambda item:(-item['level_rank'],item['id']))
        return Response({'code':0,'data':members})
