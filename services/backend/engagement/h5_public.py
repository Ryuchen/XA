"""Read-only public H5 endpoints, isolated by the middleware-selected store."""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .h5_publishing import render_html
from .models import H5Page


def published_page(token):
    return (H5Page.objects.select_related('current_version')
            .filter(public_token=token, current_version__isnull=False).first())


def not_found():
    response = Response({'code': 404, 'msg': 'H5 页面不存在或已撤回'}, status=404)
    response['Cache-Control'] = 'no-store'
    return response


class PublicH5PageView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        page = published_page(token)
        if page is None:
            return not_found()
        version = page.current_version
        data = {
            'snapshot': version.snapshot,
            'version': version.version,
            'contentSha256': version.content_sha256,
            'publishedAt': version.published_at.isoformat(),
            'copyrightConfirmed': version.copyright_confirmed,
        }
        response = Response({'code': 0, 'data': data})
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PublicH5HTMLView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        page = published_page(token)
        if page is None:
            return not_found()
        from django.http import HttpResponse
        response = HttpResponse(render_html(page.current_version), content_type='text/html; charset=utf-8')
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'no-referrer'
        response['Content-Security-Policy'] = (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        return response
