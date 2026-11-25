"""Console H5 publishing API using the existing content-management permissions."""
from django.shortcuts import get_object_or_404
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from console.mixins import EnvelopeViewSetMixin
from .db_conflict import api_lock_conflict
from .h5_publishing import (
    build_draft, page_metadata, preview, publish_page, snapshot_digest,
    version_metadata, withdraw_page,
)
from .models import H5Page, H5PageVersion


class H5PageViewSet(EnvelopeViewSetMixin, GenericViewSet):
    queryset = H5Page.objects.select_related('current_version').all()
    lookup_field = 'kind'
    lookup_value_regex = 'PRICE_LIST|ORDER_NOTICE|INTRODUCTION'
    default_perm = 'engagement:view'
    required_perms = {
        'publish': 'engagement:edit',
        'withdraw': 'engagement:edit',
        'version_receipts': ['engagement:view', 'content:view'],
    }
    http_method_names = ['get', 'post', 'head', 'options']

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if response is not None and getattr(exc, 'default_code', None):
            response.data['errorCode'] = exc.default_code
        return response

    def list(self, request, *args, **kwargs):
        pages = {page.kind: page for page in self.get_queryset()}
        rows = []
        for kind, label in H5Page.Kind.choices:
            page = pages.get(kind)
            snapshot, issues = build_draft(kind)
            row = {
                'kind': kind,
                'label': label,
                'revision': page.revision if page else 0,
                'published': bool(page and page.current_version_id),
                'publicToken': str(page.public_token) if page else None,
                'publicPath': f'/h5/pages/{page.public_token}/' if page else None,
                'jsonPath': f'/api/club/h5-pages/{page.public_token}/' if page else None,
                'currentVersion': page.current_version.version if page and page.current_version_id else None,
                'sourceStatus': 'BLOCKED' if issues else 'READY',
                'sourceIssues': issues,
                'draftDigest': snapshot_digest(snapshot),
            }
            rows.append(row)
        return Response({'code': 0, 'data': {'list': rows, 'total': len(rows)}})

    def _kind(self):
        return self.kwargs['kind']

    def _page(self):
        return get_object_or_404(self.get_queryset(), kind=self._kind())

    @action(detail=True, methods=['get'])
    def preview(self, request, kind=None):
        return Response({'code': 0, 'data': preview(kind)})

    @action(detail=True, methods=['post'])
    @api_lock_conflict
    def publish(self, request, kind=None):
        return publish_page(request, kind)

    @action(detail=True, methods=['post'])
    @api_lock_conflict
    def withdraw(self, request, kind=None):
        return withdraw_page(request, kind)

    @action(detail=True, methods=['get'], url_path='versions')
    def versions(self, request, kind=None):
        page = self._page()
        versions = page.versions.all().order_by('-version')
        return Response({'code': 0, 'data': {'list': [version_metadata(row) for row in versions],
                                             'total': versions.count()}})

    @action(detail=True, methods=['get'], url_path=r'versions/(?P<version>\d+)')
    def version_detail(self, request, kind=None, version=None):
        page = self._page()
        row = get_object_or_404(page.versions.all(), version=version)
        return Response({'code': 0, 'data': {'snapshot': row.snapshot, **version_metadata(row)}})

    @action(detail=True, methods=['get'], url_path=r'versions/(?P<version>\d+)/receipts')
    def version_receipts(self, request, kind=None, version=None):
        from console.models import ContentComplianceReceipt
        page = self._page()
        row = get_object_or_404(page.versions.all(), version=version)
        receipts = ContentComplianceReceipt.objects.filter(id__in=row.compliance_receipt_ids).order_by('id')
        data = [{
            'id': receipt.pk,
            'contentType': receipt.content_type,
            'objectId': receipt.object_id,
            'fieldName': receipt.field_name,
            'contentSha256': receipt.content_sha256,
            'ruleVersion': receipt.rule_version,
            'ruleSetSha256': receipt.rule_set_sha256,
            'matchedRuleCodes': receipt.matched_rule_codes,
            'exemptedRuleCodes': receipt.exempted_rule_codes,
            'actorLabel': receipt.actor_label,
            'createdAt': receipt.created_at.isoformat(),
        } for receipt in receipts]
        return Response({'code': 0, 'data': {'list': data, 'total': len(data)}})
