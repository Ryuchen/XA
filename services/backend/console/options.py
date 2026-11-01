"""跨菜单表单选项：按使用场景授权，仅返回选择所需字段。"""
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount, LegacyAccountMap
from announcements.models import Announcement
from orders.models import GameCategory, Order, ServiceCategory, ServiceItem
from orders.catalog import skill_service_q
from users.models import BossType, EscortLevel, EscortProfile, CustomUser
from .models import AdminRole
from .pagination import ConsolePagination
from .permissions import IsConsoleUser, get_account_permissions


OPTION_SOURCES = {
    'boss-types': (BossType, {'user:view', 'user:edit', 'boss_type:view', 'boss_type:edit'}, ('id', 'name', 'discount_rate', 'is_active')),
    'escort-levels': (EscortLevel, {'escort:view', 'escort:edit', 'service:view', 'service:edit', 'escort_level:view', 'registration:view'}, ('id', 'name', 'commission_rate', 'is_active')),
    'game-categories': (GameCategory, {'escort:view', 'escort:edit', 'service:view', 'service:edit'}, ('id', 'name', 'is_active')),
    'service-categories': (ServiceCategory, {'promotion:view', 'promotion:edit', 'service:view', 'service:edit'}, ('id', 'name', 'is_gift', 'is_active')),
    'service-items': (ServiceItem, {'order:dispatch', 'escort:edit', 'promotion:edit', 'service:view', 'banner:edit'}, ('id', 'name', 'price', 'price_unit', 'billing_provider_count', 'service_type', 'game_category_id', 'platform', 'security_level', 'provider_kind','provider_grade', 'commission_rate', 'is_active')),
    'announcements': (Announcement, {'banner:edit', 'announcement:view'}, ('id', 'title', 'is_active')),
    'roles': (AdminRole, {'admin:view', 'admin:edit', 'role:view'}, ('id', 'name', 'is_active')),
    'users': (ClubAccount, {'order:dispatch'}, ()),
    'message-recipients': (ClubAccount, {'message:push'}, ()),
    'audition-bosses': (ClubAccount, {'audition:bind_boss'}, ()),
    'audition-providers': (ClubAccount, {'audition:bind_provider'}, ()),
    'escorts': (EscortProfile, {'order:dispatch'}, ()),
}


class FormOptionsView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, resource):
        source = OPTION_SOURCES.get(resource)
        if source is None:
            return Response({'code': 404, 'msg': '选项类型不存在'}, status=404)
        model, permissions, fields = source
        if not permissions.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        qs = model.objects.all().order_by('id')
        if resource == 'boss-types':
            from engagement.models import VipTier
            qs = qs.annotate(vip_managed=Exists(VipTier.objects.filter(boss_type_id=OuterRef('pk'))))
            fields = (*fields, 'vip_managed')
        if resource in ('game-categories', 'service-items'):
            qs = qs.order_by('sort_order', 'id')
        if resource == 'service-items' and request.query_params.get('for_skills') == 'true':
            qs = qs.filter(skill_service_q())
        keyword = request.query_params.get('keyword', '').strip()
        active = request.query_params.get('is_active')
        if resource in ('users', 'message-recipients', 'audition-bosses', 'audition-providers'):
            qs = qs.filter(is_active=True, can_login=True)
            if resource in ('users', 'audition-bosses'):
                qs = qs.filter(account_type='BOSS')
                if resource == 'users': qs = qs.select_related('wallet')
            elif resource == 'audition-providers':
                qs = qs.filter(account_type='PROVIDER')
            elif resource == 'message-recipients':
                qs = qs.filter(account_type__in=('BOSS', 'PROVIDER'))
                qs = qs.annotate(recipient_mapped=Exists(LegacyAccountMap.objects.filter(
                    account_id=OuterRef('pk'), legacy_user_id__in=CustomUser.objects.values('id'),
                )))
            if keyword:
                qs = qs.filter(Q(account_no__icontains=keyword) | Q(nickname__icontains=keyword)
                               | Q(username__icontains=keyword) | Q(phone__icontains=keyword))
        elif resource == 'escorts':
            qs = qs.filter(account__is_active=True, account__can_login=True, is_verified=True).select_related('account','user','level').prefetch_related('qualifications__game_category','service_items__game_category', 'user__schedules')
            if keyword:
                qs = qs.filter(Q(display_name__icontains=keyword) | Q(account__username__icontains=keyword)
                               | Q(escort_no__icontains=keyword) | Q(account__account_no__icontains=keyword))
        else:
            if active not in (None, ''):
                qs = qs.filter(is_active=str(active).lower() in ('true', '1'))
            if keyword:
                field = 'title' if resource == 'announcements' else 'name'
                qs = qs.filter(**{field + '__icontains': keyword})
        if resource=='escorts' and request.query_params.get('service_id'):
            service=ServiceItem.objects.select_related('game_category').filter(pk=request.query_params['service_id']).first()
            if not service:return Response({'code':400,'msg':'请选择有效服务'},status=400)
            provider_gender = request.query_params.get('provider_gender', 'ANY')
            if provider_gender not in ('ANY', 'MALE', 'FEMALE'):
                return Response({'code': 400, 'msg': '陪玩性别筛选无效'}, status=400)
            if service.service_type != 'COMPANION':
                provider_gender = 'ANY'
            from orders.services import active_orders_for
            from wallet.club_rules import club_income_rules_enabled
            active = (active_orders_for(account_id=OuterRef('account_id')).order_by()
                      .values(group=Value(1)).annotate(total=Count('pk', distinct=True)).values('total'))
            qs = qs.filter(status=EscortProfile.Status.AVAILABLE).annotate(
                dispatch_active_count=Coalesce(Subquery(active, output_field=IntegerField()), 0),
            ).filter(dispatch_active_count__lt=Order.MAX_CONCURRENT_ORDERS)
            rules_enabled = club_income_rules_enabled()
            qs=[p for p in qs if not p.order_acceptance_error(service, club_rules_enabled=rules_enabled, provider_gender=provider_gender)]
        pagination = ConsolePagination()
        page = pagination.paginate_queryset(qs, request, view=self)
        if resource == 'users':
            data = [{
                'id': account.pk, 'username': account.username, 'nickname': account.nickname,
                'boss_no': account.account_no, 'wallet_balance': getattr(getattr(account, 'wallet', None), 'balance', 0),
            } for account in page]
        elif resource in ('audition-bosses', 'audition-providers'):
            data = [{'id': account.pk, 'username': account.username, 'nickname': account.nickname,
                     'account_no': account.account_no} for account in page]
        elif resource == 'message-recipients':
            data = [{
                'id': account.pk, 'username': account.username, 'nickname': account.nickname,
                'account_no': account.account_no, 'account_type': account.account_type,
                'role_display': account.get_account_type_display(), 'recipient_mapped': account.recipient_mapped,
            } for account in page]
        elif resource == 'escorts':
            from users.qualifications import qualification_rows, serialize_qualification
            data = [{
                'id': profile.pk, 'user': profile.account_id, 'display_name': profile.display_name,
                'username': profile.account.username, 'escort_no': profile.escort_no,
                'status': profile.status, 'gender': profile.gender, 'level': profile.level_id,
                'qualifications': [serialize_qualification(q) for q in qualification_rows(profile)],
            } for profile in page]
        else:
            data = [{field: getattr(item, field) for field in fields} for item in page]
        return pagination.get_paginated_response(data)
