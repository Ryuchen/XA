"""Platform-only business certificate evidence and first-opening readiness."""
from collections.abc import Mapping
from hashlib import sha256
from io import BytesIO
from zoneinfo import ZoneInfo

from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from common.catalog_images import readable_image
from .models import PlatformAuditEvent, Store, StoreBusinessCertificate
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_store_management import PlatformStoreManagementBase


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
CERTIFICATE_TIMEZONE = ZoneInfo('Asia/Shanghai')


def expired(expires_on, now):
    return expires_on is not None and timezone.localdate(now, CERTIFICATE_TIMEZONE) > expires_on


def _document_matches(record):
    document = record.document
    return (record.content_type in IMAGE_TYPES.values() and bool(document)
            and sha256(document).hexdigest() == record.sha256)


def _metadata(record):
    return {'id': record.pk, 'expires_on': record.expires_on.isoformat() if record.expires_on else None,
            'long_term': record.expires_on is None, 'supersedes_id': record.supersedes_id,
            'created_at': record.created_at.isoformat()}


def certificate_evidence(store, now):
    record = StoreBusinessCertificate.objects.filter(store=store).defer('document').first()
    if record is None:
        return {'status': 'MISSING'}
    if expired(record.expires_on, now):
        status = 'EXPIRED'
    else:
        status = 'VALID' if _document_matches(record) else 'DOCUMENT_UNAVAILABLE'
    return {**_metadata(record), 'status': status}


class CertificateUploadSerializer(serializers.Serializer):
    document = serializers.ImageField()
    expires_on = serializers.DateField(allow_null=True)
    long_term = serializers.BooleanField()
    expected_certificate_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1, allow_null=True)
    client_request_id = serializers.UUIDField()
    confirm_verified = serializers.BooleanField()

    def validate(self, attrs):
        if attrs['long_term'] == (attrs['expires_on'] is not None):
            raise serializers.ValidationError('请填写证照到期日，或明确确认原件标注长期有效')
        return attrs

    def validate_confirm_verified(self, value):
        if not value:
            raise serializers.ValidationError('请核对证照主体与本店一致后确认')
        return value

    def validate_document(self, upload):
        if upload.size > MAX_DOCUMENT_BYTES:
            raise serializers.ValidationError('证照图片大小不能超过 10 MB')
        content_type = IMAGE_TYPES.get(upload.image.format)
        upload.seek(0)
        document = upload.read(MAX_DOCUMENT_BYTES + 1)
        if not content_type or len(document) > MAX_DOCUMENT_BYTES or not readable_image(BytesIO(document)):
            raise serializers.ValidationError('请上传可读取的 PNG、JPEG 或 WebP 原件')
        return document, content_type


def _private_response(response):
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _unavailable():
    return _private_response(Response({'code': 503, 'errorCode': 'STORE_CERTIFICATE_UNAVAILABLE',
                                      'msg': '门店证照暂不可核对，请稍后重试'}, status=503))


class PlatformBusinessCertificatesView(PlatformStoreManagementBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        try:
            get_object_or_404(Store.objects.only('id'), pk=pk)
            results = [_metadata(record) for record in StoreBusinessCertificate.objects.filter(store_id=pk).defer('document')]
        except DatabaseError:
            return _unavailable()
        return _private_response(Response({'code': 0, 'data': {'current': results[0] if results else None, 'results': results}}))

    def post(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        serializer = CertificateUploadSerializer(data=request.data)
        if not isinstance(request.data, Mapping) or set(request.data) != set(serializer.fields):
            return Response({'code': 400, 'msg': '请填写完整的证照登记字段'}, status=400)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        document, content_type = data['document']
        digest = sha256(document).hexdigest()
        expected = data['expected_certificate_id']
        try:
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = get_object_or_404(Store.objects.select_for_update(), pk=pk)
                records = StoreBusinessCertificate.objects.filter(store=store).defer('document')
                prior = records.filter(client_request_id=data['client_request_id']).first()
                if prior is not None:
                    if (prior.sha256, prior.expires_on, prior.supersedes_id) != (digest, data['expires_on'], expected):
                        return Response({'code': 409, 'errorCode': 'STORE_CERTIFICATE_REQUEST_CONFLICT',
                                         'msg': '同一请求编号已用于其他证照资料'}, status=409)
                    return _private_response(Response({'code': 0, 'data': _metadata(prior)}))
                current_id = records.values_list('pk', flat=True).first()
                if current_id != expected:
                    return Response({'code': 409, 'errorCode': 'STORE_CERTIFICATE_CHANGED',
                                     'msg': '当前证照已变化，请重新读取后确认'}, status=409)
                record = StoreBusinessCertificate.objects.create(store=store, confirmed_by=actor,
                    supersedes_id=current_id, client_request_id=data['client_request_id'], expires_on=data['expires_on'],
                    document=document, content_type=content_type, sha256=digest)
                PlatformAuditEvent.objects.create(account=actor, action='store.certificate.recorded',
                    target_store_slug=store.slug, target_type='store_business_certificate', target_id=str(record.pk),
                    detail={'expires_on': _metadata(record)['expires_on'], 'supersedes_id': current_id,
                            'request_id': str(record.client_request_id), 'sha256': digest})
        except AccountChangeError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return _unavailable()
        return _private_response(Response({'code': 0, 'data': _metadata(record)}, status=201))


class PlatformBusinessCertificateDocumentView(PlatformStoreManagementBase):
    def get(self, request, pk, certificate_id):
        if not self.allowed(request):
            return self.denied()
        try:
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                record = get_object_or_404(StoreBusinessCertificate.objects.select_related('store'),
                                           pk=certificate_id, store_id=pk)
                if not _document_matches(record):
                    return _unavailable()
                PlatformAuditEvent.objects.create(account=actor, action='store.certificate.downloaded',
                    target_store_slug=record.store.slug, target_type='store_business_certificate', target_id=str(record.pk))
        except AccountChangeError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return _unavailable()
        extension = record.content_type.split('/')[1]
        response = HttpResponse(bytes(record.document), content_type=record.content_type)
        response['Content-Disposition'] = f'attachment; filename="business-certificate-{record.pk}.{extension}"'
        return _private_response(response)
