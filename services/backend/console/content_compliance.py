"""Store-local read access to content-only compliance evidence."""
from urllib.parse import urlencode

from django.core.paginator import EmptyPage, Paginator
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.compliance import CONTENT_FIELDS, normalize_actor_label
from .models import ContentComplianceReceipt
from .permissions import HasConsolePerm, IsConsoleUser


class ContentComplianceReceiptsView(APIView):
    permission_classes = [IsConsoleUser, HasConsolePerm]
    default_perm = 'content:view'

    def invalid(self, message):
        return Response({'code': 400, 'errorCode': 'VALIDATION_ERROR', 'msg': message}, status=400)

    def single_param(self, request, name, default=''):
        values = request.query_params.getlist(name)
        if len(values) > 1:
            raise ValidationError(f'{name} 不能重复')
        return values[0] if values else default

    def positive_param(self, request, name, default, maximum):
        raw = self.single_param(request, name, str(default))
        if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= maximum:
            raise ValidationError(f'{name} 无效')
        return int(raw)

    def link(self, request, page, *, filters, page_size):
        return request.build_absolute_uri(request.path) + '?' + urlencode({**filters, 'page': page, 'page_size': page_size})

    def get(self, request):
        if any(key not in {'content_type', 'object_id', 'field_name', 'page', 'page_size'}
               for key in request.query_params):
            return self.invalid('存在不支持的查询参数')
        try:
            content_type = self.single_param(request, 'content_type')
            field_name = self.single_param(request, 'field_name')
            object_id = self.single_param(request, 'object_id')
            page_number = self.positive_param(request, 'page', 1, 2_147_483_647)
            page_size = self.positive_param(request, 'page_size', 20, 100)
        except ValidationError as exc:
            return self.invalid(str(exc.detail[0]))
        if content_type and not 0 < len(content_type) <= 32:
            return self.invalid('content_type 无效')
        if field_name and field_name not in CONTENT_FIELDS:
            return self.invalid('field_name 无效')
        if object_id and (not object_id.isascii() or not object_id.isdigit() or int(object_id) <= 0):
            return self.invalid('object_id 无效')
        filters = {}
        if content_type:
            filters['content_type'] = content_type
        if field_name:
            filters['field_name'] = field_name
        if object_id:
            filters['object_id'] = int(object_id)
        paginator = Paginator(ContentComplianceReceipt.objects.filter(**filters).order_by('-created_at', '-pk'), page_size)
        try:
            page = paginator.page(page_number)
        except EmptyPage:
            return self.invalid('page 超出范围')
        items = [{
            'id': row.pk,
            'contentType': row.content_type,
            'objectId': row.object_id,
            'fieldName': row.field_name,
            'contentSha256': row.content_sha256,
            'ruleVersion': row.rule_version,
            'ruleSetSha256': row.rule_set_sha256,
            'matchedRuleCodes': row.matched_rule_codes,
            'exemptedRuleCodes': row.exempted_rule_codes,
            'actorType': row.actor_type,
            'actorId': row.actor_id,
            'actorAccountId': str(row.actor_account_id) if row.actor_account_id else None,
            'actorLabel': normalize_actor_label(
                row.actor_type, row.actor_id or row.actor_account_id, row.actor_label,
            ),
            'createdAt': row.created_at.isoformat(),
        } for row in page.object_list]
        return Response({'code': 0, 'data': {
            'results': items,
            'count': paginator.count,
            'next': self.link(request, page.next_page_number(), filters=filters, page_size=page_size)
                   if page.has_next() else None,
            'previous': self.link(request, page.previous_page_number(), filters=filters, page_size=page_size)
                        if page.has_previous() else None,
        }})
