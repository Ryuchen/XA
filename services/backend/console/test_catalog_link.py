"""Game/category -> operator profile -> provider skills -> boss selection contracts."""
from django.test import override_settings
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.permissions import ALL_PERMISSIONS
from console.serializers import AdminCreateEscortSerializer
from orders.models import GameCategory, ServiceFavorite
from orders.tests.factories import make_console_user, make_provider, make_user, make_service, make_service_category
from users.models import EscortLevel


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class CatalogLinkTests(APITestCase):
    def setUp(self):
        self.staff = make_console_user(ALL_PERMISSIONS)
        self.provider = make_provider()
        self.boss = make_user()
        self.accounts = {}
        for user in (self.staff, self.provider, self.boss):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
        self.profile = self.provider.escort_profile
        self.profile.account = self.accounts[self.provider.pk]
        self.profile.save()
        membership = self.staff.admin_membership
        membership.account = self.accounts[self.staff.pk]
        membership.save()
        self.game = GameCategory.objects.create(name='三角洲行动', sort_order=5)
        self.other_game = GameCategory.objects.create(name='王者荣耀', sort_order=1)
        self.category = make_service_category()
        self.service = make_service(game_category=self.game, service_category=self.category, price_unit='小时')
        self.login(self.staff)

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_admin_skills_link_games_and_are_visible_on_both_clients(self):
        result = self.client.patch(f'/api/admin/escorts/{self.profile.pk}/', {
            'service_items': [self.service.pk], 'game_categories': [],
        }, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(result.data['data']['game_categories'], [self.game.pk])
        self.login(self.provider)
        self.assertEqual(self.client.get('/api/users/escorts/me/').data['data']['service_item_ids'], [self.service.pk])
        self.login(self.boss)
        result = self.client.get('/api/users/escorts/', {'game_category': self.game.pk, 'directory': 1})
        self.assertEqual(result.data['data'][0]['game_category_names'], [self.game.name])
        self.assertEqual(len(self.client.get('/api/users/escorts/', {'service_item': self.service.pk}).data['data']), 1)

    def test_multipart_empty_arrays_clear_existing_relations(self):
        self.profile.service_items.add(self.service)
        self.profile.game_categories.add(self.game)
        result = self.client.patch(f'/api/admin/escorts/{self.profile.pk}/', {
            'service_items': '[]', 'game_categories': '[]', 'bio': '保留其他资料',
        }, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertFalse(self.profile.service_items.exists())
        self.assertFalse(self.profile.game_categories.exists())

    def test_invalid_new_skills_do_not_partially_update_profile(self):
        gift = make_service(game_category=self.game, service_category=make_service_category(is_gift=True))
        addon = make_service(game_category=self.game, is_addon=True)
        for item in (gift, addon):
            result = self.client.patch(f'/api/admin/escorts/{self.profile.pk}/', {
                'display_name': '不得写入', 'service_items': [item.pk],
            }, format='json')
            self.assertEqual(result.status_code, 400)
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.display_name, '不得写入')

    def test_create_normalizes_games_and_rejects_disabled_service(self):
        serializer = AdminCreateEscortSerializer(data={'username': 'new-provider', 'password': 'test-password',
            'display_name': '新陪玩', 'service_items': [self.service.pk]})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['game_categories'], [self.game])
        self.game.is_active = False
        self.game.save()
        serializer = AdminCreateEscortSerializer(data={'username': 'new-provider', 'password': 'test-password',
            'display_name': '新陪玩', 'service_items': [self.service.pk]})
        self.assertFalse(serializer.is_valid())

    def test_disabled_parent_hides_catalog_detail_favorites_and_skills(self):
        self.profile.service_items.add(self.service)
        ServiceFavorite.objects.create(user=self.boss, account=self.accounts[self.boss.pk], service=self.service)
        for parent in (self.game, self.category):
            parent.is_active = False
            parent.save()
            self.login(self.boss)
            self.assertEqual(self.client.get('/api/orders/services/').data['data'], [])
            self.assertEqual(self.client.get(f'/api/orders/services/{self.service.pk}/').data['code'], 404)
            self.assertEqual(self.client.get('/api/orders/favorites/').data['data'], [])
            self.assertEqual(self.client.get('/api/users/escorts/', {'service_item': self.service.pk}).data['data'], [])
            quote = self.client.post('/api/orders/quote/', {'service_id': self.service.pk}, format='json')
            self.assertNotEqual(quote.data['code'], 0)
            self.login(self.provider)
            self.assertEqual(self.client.get('/api/users/escorts/skill-options/').data['data'], [])
            invalid = self.client.patch('/api/users/escorts/me/', {'service_items': [self.service.pk], 'bio': '不得写入'}, format='json')
            self.assertEqual(invalid.data['code'], 400)
            parent.is_active = True
            parent.save()

    def test_skill_metadata_and_display_order(self):
        make_service(game_category=self.other_game)
        self.login(self.provider)
        rows = self.client.get('/api/users/escorts/skill-options/').data['data']
        self.assertEqual([row['game_category_id'] for row in rows], [self.other_game.pk, self.game.pk])
        self.assertEqual(rows[1]['items'][0]['price_unit'], '小时')
        self.assertIn('required_level_name', rows[1]['items'][0])

    def test_reassigning_service_updates_provider_game_membership(self):
        self.profile.service_items.add(self.service)
        self.profile.game_categories.add(self.game)
        result = self.client.patch(f'/api/admin/service-items/{self.service.pk}/', {
            'game_category': self.other_game.pk,
        }, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(list(self.profile.game_categories.values_list('pk', flat=True)), [self.other_game.pk])

    def test_pending_pool_and_grab_share_skill_guard(self):
        from datetime import timedelta
        from django.utils import timezone
        from orders.tests.factories import make_order
        from orders.models import Order
        order = make_order(self.boss, service=self.service, customer_account=self.accounts[self.boss.pk],
            payment_status=Order.PaymentStatus.PAID)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        self.login(self.provider)
        result = self.client.get('/api/orders/orders/', {'status': 'pending'})
        self.assertNotIn(order.pk, [row['id'] for row in result.data['data']])
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertNotEqual(result.data['code'], 0)
        self.profile.service_items.add(self.service)
        result = self.client.get('/api/orders/orders/', {'status': 'pending'})
        self.assertIn(order.pk, [row['id'] for row in result.data['data']])

    def test_category_counts_filter_and_delete_guard(self):
        self.profile.game_categories.add(self.game)
        self.other_game.is_active = False
        self.other_game.save()
        rows = self.client.get('/api/admin/game-categories/', {'is_active': 'true'}).data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['service_count'], rows[0]['active_service_count'], rows[0]['escort_count']), (1, 1, 1))
        self.assertEqual(self.client.delete(f'/api/admin/game-categories/{self.game.pk}/').status_code, 400)
        rows = self.client.get('/api/admin/escorts/', {'game_category': self.other_game.pk}).data['data']['list']
        self.assertEqual(rows, [])

    def test_service_selection_obeys_level_and_skill_readiness(self):
        self.assertEqual(self.profile.order_acceptance_error(self.service), '陪玩尚未配置该服务技能')
        self.profile.service_items.add(self.service)
        self.service.required_level = EscortLevel.objects.create(name='高级', sort_order=10, commission_rate=20)
        self.service.save()
        self.login(self.boss)
        self.assertEqual(self.client.get('/api/users/escorts/', {'service_item': self.service.pk}).data['data'], [])
        for params in ({'service_item': 'wrong'}, {'game_category': '9' * 100}):
            self.assertEqual(self.client.get('/api/users/escorts/', params).status_code, 400)
