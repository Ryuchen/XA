from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from orders.models import Gameplay, ServiceItem
from orders.tests.factories import make_console_user

@override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
class CatalogArchiveTests(TestCase):
    def test_archive_hides_products_and_can_restore(self):
        client = APIClient()
        client.force_authenticate(make_console_user(['service:view','service:delete','service:edit']))
        g = Gameplay.objects.create(name='历史体验单',code='old-experience')
        s = ServiceItem.objects.create(name='旧档位',price=9800,gameplay=g)
        response = client.delete(f'/api/admin/gameplays/{g.id}/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['code'],0)
        g.refresh_from_db()
        self.assertTrue(g.is_archived)
        self.assertFalse(g.is_active)
        self.assertTrue(ServiceItem.objects.filter(pk=s.pk).exists())
        self.assertEqual(client.get(f'/api/admin/gameplays/{g.id}/').status_code,404)
        self.assertEqual(client.get(f'/api/admin/service-items/{s.id}/').status_code,404)
        response = client.patch(f'/api/admin/gameplays/{g.id}/?is_archived=true',{'is_active':True},format='json')
        self.assertEqual(response.status_code,400)
        response = client.post(f'/api/admin/gameplays/{g.id}/restore/')
        self.assertEqual(response.status_code,200,response.data)
        g.refresh_from_db()
        self.assertFalse(g.is_archived)
        self.assertFalse(g.is_active)

    def test_readonly_staff_cannot_archive(self):
        client = APIClient()
        client.force_authenticate(make_console_user(['service:view']))
        g = Gameplay.objects.create(name='不可归档测试',code='archive-rbac')
        self.assertEqual(client.delete(f'/api/admin/gameplays/{g.id}/').status_code,403)
        g.refresh_from_db()
        self.assertFalse(g.is_archived)
