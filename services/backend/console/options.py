"""跨菜单表单选项：按使用场景授权，仅返回选择所需字段。"""
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from announcements.models import Announcement
from orders.models import GameCategory, ServiceCategory, ServiceItem
from orders.catalog import skill_service_q
from users.models import BossType, EscortLevel, EscortProfile
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
        if resource in ('game-categories', 'service-items'):
            qs = qs.order_by('sort_order', 'id')
        if resource == 'service-items' and request.query_params.get('for_skills') == 'true':
            qs = qs.filter(skill_service_q())
        keyword = request.query_params.get('keyword', '').strip()
        active = request.query_params.get('is_active')
        if resource in ('users', 'message-recipients'):
            qs = qs.filter(is_active=True, can_login=True)
            if resource == 'users':
                qs = qs.filter(account_type='BOSS').select_related('wallet')
            else:
                qs = qs.filter(account_type__in=('BOSS', 'PROVIDER'))
            if keyword:
                qs = qs.filter(Q(account_no__icontains=keyword) | Q(nickname__icontains=keyword)
                               | Q(username__icontains=keyword) | Q(phone__icontains=keyword))
        elif resource == 'escorts':
            qs = qs.filter(account__is_active=True, account__can_login=True, is_verified=True).select_related('account','user','level').prefetch_related('qualifications__game_category','service_items__game_category')
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
            qs=[p for p in qs if not p.order_acceptance_error(service)]
        pagination = ConsolePagination()
        page = pagination.paginate_queryset(qs, request, view=self)
        if resource == 'users':
            data = [{
                'id': account.pk, 'username': account.username, 'nickname': account.nickname,
                'boss_no': account.account_no, 'wallet_balance': getattr(getattr(account, 'wallet', None), 'balance', 0),
            } for account in page]
        elif resource == 'message-recipients':
            data = [{
                'id': account.pk, 'username': account.username, 'nickname': account.nickname,
                'account_no': account.account_no, 'account_type': account.account_type,
                'role_display': account.get_account_type_display(),
            } for account in page]
        elif resource == 'escorts':
            data = [{
                'id': profile.pk, 'user': profile.account_id, 'display_name': profile.display_name,
                'username': profile.account.username, 'escort_no': profile.escort_no,
                'status': profile.status, 'level': profile.level_id,
                'qualifications':[__import__('users.qualifications',fromlist=['serialize_qualification']).serialize_qualification(q) for q in profile.qualifications.select_related('game_category').all()],
            } for profile in page]
        else:
            data = [{field: getattr(item, field) for field in fields} for item in page]
        return pagination.get_paginated_response(data)
