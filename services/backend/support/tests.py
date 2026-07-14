from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from console.serializers import AdminSupportCardSerializer

from .models import SupportContactCard


User = get_user_model()


class SupportContactCardApiTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='support-customer', password='pass1234')
        self.client.force_authenticate(self.user)

    def test_contact_card_returns_wecom_configuration(self):
        SupportContactCard.objects.create(
            name='企业微信客服',
            wecom_corp_id='ww1234567890',
            wecom_service_url='https://work.weixin.qq.com/kfid/kfc123456',
        )
        response = self.client.get('/api/support/contact-card/')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(response.data['data']['wecom_corp_id'], 'ww1234567890')
        self.assertEqual(
            response.data['data']['wecom_service_url'],
            'https://work.weixin.qq.com/kfid/kfc123456',
        )

    def test_contact_card_list_returns_only_active_cards_in_order(self):
        SupportContactCard.objects.create(name='客服B', sort_order=2)
        SupportContactCard.objects.create(name='客服A', sort_order=1)
        SupportContactCard.objects.create(name='离岗客服', sort_order=0, is_active=False)
        response = self.client.get('/api/support/contact-cards/')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual([item['name'] for item in response.data['data']], ['客服A', '客服B'])

    def test_admin_configuration_requires_corp_id_and_url_together(self):
        serializer = AdminSupportCardSerializer(data={
            'name': '配置不完整的客服',
            'wecom_corp_id': 'ww1234567890',
            'wecom_service_url': '',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)

    def test_admin_configuration_rejects_non_kfid_url(self):
        serializer = AdminSupportCardSerializer(data={
            'name': '错误链接客服',
            'wecom_corp_id': 'ww1234567890',
            'wecom_service_url': 'https://example.com/customer-service',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('wecom_service_url', serializer.errors)
