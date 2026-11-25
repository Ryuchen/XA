import hashlib
import json

from django.contrib import admin
from django.forms.models import model_to_dict
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from club_accounts.services import get_or_create_account_for_legacy_user
from console.middleware import MAX_BODY_CHARS, AdminAuditMiddleware
from console.models import AdminAuditLog
from orders.admin import ServiceItemAdmin
from orders.models import ServiceItem
from orders.tests.factories import make_console_user, make_service, make_superuser


def sha256_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class ComplianceAuditRedactionTests(TestCase):
    def setUp(self):
        self.operator = make_console_user(['service:view', 'service:edit'])
        self.account = get_or_create_account_for_legacy_user(self.operator)
        self.operator.admin_membership.account = self.account
        self.operator.admin_membership.save(update_fields=['account'])

    def audit(self, payload):
        request = RequestFactory().post(
            '/api/admin/service-items/',
            data=json.dumps(payload, ensure_ascii=False),
            content_type='application/json',
            HTTP_HOST='admin.esports.club',
            REMOTE_ADDR='127.0.0.1',
        )
        request.account = self.account
        request.legacy_user = self.operator
        response = AdminAuditMiddleware(lambda request: HttpResponse(status=201))(request)
        self.assertEqual(response.status_code, 201)
        return AdminAuditLog.objects.latest('pk')

    def test_top_level_and_nested_compliance_text_are_replaced_by_digests(self):
        name = '审计服务'
        display_name = '合规陪玩昵称'
        introduction = '本店提供合规陪玩服务'
        rules = '最终解释权归本店'
        details_html = '<p>本店可单方面变更服务内容</p>'
        nested_rules = '活动最终以本店审核为准'
        nested_details_html = '<b>条款以本店通知为准</b>'
        nested_name = '嵌套合规名称'
        record = self.audit({
            'name': name,
            'display_name': display_name,
            'introduction': introduction,
            'price': 10000,
            'rules': rules,
            'details_html': details_html,
            'password': 'Audit-secret-928!',
            'metadata': {
                'note': '保留普通备注',
                'items': [{
                    'name': nested_name,
                    'rules': nested_rules,
                    'details_html': nested_details_html,
                    'password': 'Nested-secret-928!',
                }],
            },
        })

        self.assertEqual(record.request_body, {
            'name': {'sha256': sha256_text(name)},
            'display_name': {'sha256': sha256_text(display_name)},
            'introduction': {'sha256': sha256_text(introduction)},
            'price': 10000,
            'rules': {'sha256': sha256_text(rules)},
            'details_html': {'sha256': sha256_text(details_html)},
            'password': '***',
            'metadata': {
                'note': '保留普通备注',
                'items': [{
                    'name': {'sha256': sha256_text(nested_name)},
                    'rules': {'sha256': sha256_text(nested_rules)},
                    'details_html': {'sha256': sha256_text(nested_details_html)},
                    'password': '***',
                }],
            },
        })
        serialized = json.dumps(record.request_body, ensure_ascii=False)
        for content in (name, display_name, introduction, rules, details_html,
                        nested_name, nested_rules, nested_details_html,
                        'Audit-secret-928!', 'Nested-secret-928!'):
            self.assertNotIn(content, serialized)

    def test_read_only_probe_never_writes_audit_or_changes_content(self):
        service = make_service(name='只读探针服务', rules='原有规则', details_html='<p>原有详情</p>')
        before = AdminAuditLog.objects.count()
        request = RequestFactory().get(
            f'/api/admin/service-items/{service.pk}/',
            data={'display_name': '探针昵称正文', 'introduction': '探针介绍正文'},
            HTTP_HOST='admin.esports.club',
            REMOTE_ADDR='127.0.0.1',
        )
        request.account = self.account
        request.legacy_user = self.operator
        response = AdminAuditMiddleware(lambda request: HttpResponse(status=200))(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AdminAuditLog.objects.count(), before)
        service.refresh_from_db()
        self.assertEqual((service.name, service.rules, service.details_html),
                         ('只读探针服务', '原有规则', '<p>原有详情</p>'))

    def test_redaction_precedes_the_existing_body_size_limit(self):
        rules = '合规正文' * 2000
        details_html = '<p>合规详情</p>' * 2000
        name = '长正文服务'
        record = self.audit({'name': name, 'rules': rules, 'details_html': details_html})
        self.assertEqual(record.request_body, {
            'name': {'sha256': sha256_text(name)},
            'rules': {'sha256': sha256_text(rules)},
            'details_html': {'sha256': sha256_text(details_html)},
        })
        self.assertLess(len(json.dumps(record.request_body, ensure_ascii=False)), MAX_BODY_CHARS)

        oversized = self.audit({'rules': '短正文', 'note': 'x' * (MAX_BODY_CHARS + 1)})
        self.assertEqual(oversized.request_body, {'_truncated': True})


class ServiceItemComplianceAdminTests(TestCase):
    def setUp(self):
        self.operator = make_superuser()
        self.client.force_login(self.operator)

    def test_admin_forms_are_read_only_and_posts_cannot_replace_compliance_text(self):
        service = make_service(
            name='后台服务',
            rules='原有合规规则',
            details_html='<p>原有合规详情</p>',
        )
        model_admin = admin.site._registry[ServiceItem]
        self.assertIsInstance(model_admin, ServiceItemAdmin)
        request = RequestFactory().get(reverse('admin:orders_serviceitem_change', args=[service.pk]))
        request.user = self.operator
        for obj in (None, service):
            form = model_admin.get_form(request, obj)
            self.assertNotIn('rules', form.base_fields)
            self.assertNotIn('details_html', form.base_fields)

        form = model_admin.get_form(request, service)
        values = model_to_dict(service)
        payload = {
            name: values[name] if values[name] is not None else ''
            for name in form.base_fields
        }
        payload.update({
            'name': '后台改名',
            'rules': '最终解释权归本店',
            'details_html': '<p>本店可单方面变更服务内容</p>',
        })
        response = self.client.post(
            reverse('admin:orders_serviceitem_change', args=[service.pk]), payload,
        )
        self.assertEqual(response.status_code, 302, response.content)
        service.refresh_from_db()
        self.assertEqual(service.name, '后台改名')
        self.assertEqual(service.rules, '原有合规规则')
        self.assertEqual(service.details_html, '<p>原有合规详情</p>')
