"""Draft editor for explicitly linked, store-local game commission rules."""
from django.conf import settings
from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from orders.game_commission import preview_rate, validate_rules
from orders.models import GameCategory, GameCommissionProfile, Gameplay, ServiceItem
from .models import AdminAuditLog
from .pagination import ConsolePagination
from .permissions import IsConsoleUser, get_account_permissions


def _preview(profile, service, *, validated_rules=None):
    if profile.game != GameCommissionProfile.Game.DELTA:
        return preview_rate(profile, service, validated_rules=validated_rules)
    from orders.delta_personal_commission import freeze_rules
    frozen = freeze_rules(service, profile)
    return {'game_category_id': profile.game_category_id, 'game': profile.game,
            'version': profile.version, 'rate': None,
            'scope': {key: frozen['service_scope'][key] for key in
                      ('provider_kind', 'platform', 'security_level', 'gameplay_id')},
            'grade_rates': {row['grade']: row['rate'] for row in frozen['rules']}}


def _payment_enabled():
    return getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False)


def _configuration_status():
    return 'PAYMENT_ENFORCED' if _payment_enabled() else 'DRAFT_NOT_APPLIED'


def _serialized(row):
    if row is None:
        return None
    return {'game_category_id': row.game_category_id, 'game': row.game,
            'rules': row.rules, 'version': row.version}


class GameCommissionCatalogView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看游戏抽成配置'}, status=403)
        profiles = {p.game_category_id: p for p in GameCommissionProfile.objects.all()}
        return Response({'code': 0, 'data': {'capability': 'GAME_COMMISSION_DRAFT_V1',
            'payment_enforced': _payment_enabled(),
            'games': [{'id': game.pk, 'name': game.name, 'is_active': game.is_active,
                       'game': profiles[game.pk].game if game.pk in profiles else None}
                      for game in GameCategory.objects.order_by('sort_order', 'id')],
            'gameplays': list(Gameplay.objects.order_by('sort_order', 'id').values('id', 'name', 'is_active', 'is_archived'))}})


class GameCommissionCoverageView(APIView):
    """Read-only coverage diagnostic; not an activation or settlement gate."""
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看游戏抽成配置'}, status=403)
        category_filter = request.query_params.get('result', 'all')
        if category_filter not in ('all', 'matched', 'issues', 'special'):
            return Response({'code': 400, 'msg': '请选择有效的核对结果'}, status=400)
        game_id = request.query_params.get('game_category_id', '')
        if game_id and (not game_id.isascii() or not game_id.isdigit() or len(game_id) > 19
                        or not 1 <= int(game_id) <= 9223372036854775807):
            return Response({'code': 400, 'msg': '请选择有效的游戏类目'}, status=400)
        from orders.catalog import visible_service_q
        profiles = {row.game_category_id: row for row in GameCommissionProfile.objects.all()}
        checked = {}
        for category_id, profile in profiles.items():
            try:
                checked[category_id] = (validate_rules(profile.game, profile.rules), None)
            except (TypeError, ValueError) as exc:
                checked[category_id] = (None, str(exc))
        services = ServiceItem.objects.filter(visible_service_q()).select_related(
            'game_category', 'service_category', 'gameplay').order_by('sort_order', 'id')
        if game_id:
            services = services.filter(game_category_id=int(game_id))
        counts = {'all': 0, 'matched': 0, 'issues': 0, 'special': 0}
        rows = []
        for service in services:
            if service.is_addon:
                group, code, message = 'special', 'ADDON', '加购沿用主单相关规则，需单独核对'
            elif service.service_category_id and service.service_category.is_gift:
                group, code, message = 'special', 'GIFT', '礼物使用独立抽成规则，需单独核对'
            elif service.experience_kind or service.commission_policy == 'EXPERIENCE':
                group, code, message = 'special', 'EXPERIENCE', '体验套餐使用独立规则，需单独核对'
            elif service.commission_override:
                group, code, message = 'issues', 'SERVICE_OVERRIDE', '服务独立抽成与游戏规则的衔接尚待处理'
            elif not service.game_category_id:
                group, code, message = 'issues', 'NO_GAME', '服务尚未关联游戏'
            elif service.game_category_id not in profiles:
                group, code, message = 'issues', 'NO_PROFILE', '本店该游戏尚未配置抽成草稿'
            elif checked[service.game_category_id][1]:
                group, code, message = 'issues', 'INVALID_PROFILE', checked[service.game_category_id][1]
            else:
                profile = profiles[service.game_category_id]
                try:
                    match = _preview(profile, service, validated_rules=checked[service.game_category_id][0])
                except (TypeError, ValueError) as exc:
                    group, code, message = 'issues', 'NO_MATCH', str(exc)
                else:
                    group = 'matched'
                    code = 'MATCHED_ACTIVE' if _payment_enabled() else 'MATCHED_DRAFT'
                    if profile.game == GameCommissionProfile.Game.DELTA:
                        message = '已列出已配置档位；按陪玩本人资格匹配，未列出的档位未配置'
                    else:
                        message = '已匹配；普通服务新订单按规则计算抽成' if _payment_enabled() else '已匹配草稿，尚未用于订单计算'
            counts['all'] += 1
            counts[group] += 1
            if category_filter not in ('all', group):
                continue
            row = {'id': service.pk, 'name': service.name,
                   'game_category_id': service.game_category_id,
                   'game_name': service.game_category.name if service.game_category_id else '',
                   'gameplay_name': service.gameplay.name if service.gameplay_id else '',
                   'provider_kind': service.provider_kind, 'platform': service.platform,
                   'security_level': service.security_level, 'grade': service.provider_grade,
                   'group': group, 'code': code, 'message': message}
            if group == 'matched':
                row.update(rate=match['rate'], version=match['version'], game=match['game'])
                if 'grade_rates' in match:
                    row['grade_rates'] = match['grade_rates']
            rows.append(row)
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(rows, request)
        response = paginator.get_paginated_response(page)
        response.data['data'].update(counts=counts, status=_configuration_status())
        return response


class GameCommissionConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, game_id):
        if not {'dictionary:view', 'dictionary:edit', 'service:view'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看游戏抽成配置'}, status=403)
        game = GameCategory.objects.filter(pk=game_id).first()
        if game is None:
            return Response({'code': 404, 'msg': '游戏类目不存在'}, status=404)
        profile = GameCommissionProfile.objects.filter(game_category_id=game_id).first()
        return Response({'code': 0, 'data': {'game_category_id': game.pk, 'name': game.name,
            'active': game.is_active, 'configuration': _serialized(profile),
            'status': _configuration_status()}})

    def put(self, request, game_id):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改游戏抽成配置'}, status=403)
        game, version = request.data.get('game'), request.data.get('version')
        if type(version) is not int or version < 0:
            return Response({'code': 400, 'msg': '配置版本须为非负整数'}, status=400)
        try:
            rules = validate_rules(game, request.data.get('rules'))
        except (ValueError, TypeError) as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        try:
            with transaction.atomic():
                category = GameCategory.objects.select_for_update().filter(pk=game_id).first()
                if category is None:
                    return Response({'code': 404, 'msg': '游戏类目不存在'}, status=404)
                row = GameCommissionProfile.objects.select_for_update().filter(game_category=category).first()
                old = _serialized(row)
                if (row.version if row else 0) != version:
                    return Response({'code': 409, 'errorCode': 'CONFIG_CHANGED',
                                     'msg': '配置已变化，请刷新后重新核对'}, status=409)
                if row is None:
                    row = GameCommissionProfile(game_category=category, game=game, version=1, rules=rules)
                else:
                    row.game, row.rules, row.version = game, rules, row.version + 1
                row.save()
                new = _serialized(row)
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='game-commission-draft', object_id=str(category.pk),
                    request_body={'before': old, 'after': new}, status_code=200)
        except IntegrityError:
            return Response({'code': 409, 'msg': '该游戏分类已绑定其他类目或配置发生变化'}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {'configuration': new, 'status': _configuration_status()},
                         'msg': '配置已保存；普通服务新订单使用新规则' if _payment_enabled() else '草稿已保存；尚未接入订单计算'})


class GameCommissionPreviewView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, game_id, service_id):
        if not {'dictionary:view', 'dictionary:edit', 'service:view'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看游戏抽成配置'}, status=403)
        profile = GameCommissionProfile.objects.filter(game_category_id=game_id).first()
        service = ServiceItem.objects.filter(pk=service_id, game_category_id=game_id).first()
        if profile is None or service is None:
            return Response({'code': 404, 'msg': '草稿或本游戏服务不存在'}, status=404)
        try:
            data = _preview(profile, service)
        except (ValueError, TypeError) as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        return Response({'code': 0, 'data': {**data, 'status': _configuration_status()}})
