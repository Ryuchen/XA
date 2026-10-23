from datetime import timedelta
from django.test import TestCase,override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from users.models import ProviderQualification,EscortProfile
from users.qualifications import grade_options,service_requirement,matching_qualification,qualified_order_q,authorized_skill_q
from orders.models import GameCategory,Order
from orders.pricing import resolve_commission_rate,club_provider_specs
from orders.tests.factories import make_user,make_provider,make_service
from console.serializers import AdminEscortSerializer,AdminServiceItemSerializer

class ProviderQualificationRulesTest(APITestCase):
    def setUp(self):
        self.game=GameCategory.objects.create(name='三角洲测试')
        self.boss=make_user();self.boss_account=get_or_create_account_for_legacy_user(self.boss)
        self.user=make_provider(deposit_mode='EARNINGS')
        self.account=get_or_create_account_for_legacy_user(self.user)
        self.profile=EscortProfile.objects.get(user=self.user)
        self.profile.account=self.account;self.profile.save(update_fields=['account'])
        self.service=make_service(game_category=self.game,provider_kind='GUARD',provider_grade='MASTER',platform='PC',security_level='CONFIDENTIAL',commission_policy='REGULAR',catalog_family='测试护航')
        self.profile.service_items.add(self.service)
    def qualification(self,platform='PC',security='CONFIDENTIAL',grade='LEGEND',kind='GUARD',game=None):
        return ProviderQualification.objects.create(profile=self.profile,game_category=game or self.game,provider_kind=kind,platform=platform,security_level=security,grade=grade)
    def order(self):
        order=Order.objects.create(customer=self.boss,customer_account=self.boss_account,service=self.service,amount=10000,commission_rate=resolve_commission_rate(self.service,self.profile,None),created_at=timezone.now()-timedelta(hours=1))
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=1));order.refresh_from_db();return order
    def test_multi_scope_does_not_share_platform_difficulty_or_game(self):
        self.qualification()
        self.qualification('MOBILE','TOP_SECRET','MASTER')
        self.assertTrue(self.profile.can_take_service(self.service))
        self.service.platform='MOBILE';self.assertFalse(self.profile.can_take_service(self.service))
        self.service.security_level='TOP_SECRET';self.assertTrue(self.profile.can_take_service(self.service))
        self.service.provider_grade='LEGEND';self.assertFalse(self.profile.can_take_service(self.service))
        self.service.game_category=GameCategory.objects.create(name='另一游戏');self.assertFalse(self.profile.can_take_service(self.service))
    def test_missing_qualification_does_not_fall_back_to_global_level(self):
        from users.models import EscortLevel
        self.profile.level=EscortLevel.objects.create(name='明星',commission_rate=15,sort_order=99)
        self.assertFalse(self.profile.can_take_service(self.service))
    def test_pass_never_grants_qualification(self):
        self.profile.pass_tier='BLACK';self.profile.pass_expires_at=timezone.now()+timedelta(days=30)
        self.assertFalse(self.profile.can_take_service(self.service))
    def test_order_tier_commission_matrix_ignores_higher_provider_grade(self):
        for policy in ['REGULAR','FUN','LEGACY','COMPANION']:
            self.service.commission_policy=policy
            for platform,security,master,legend in [('PC','CONFIDENTIAL',25,20),('MOBILE','TOP_SECRET',25,20),('MOBILE','CONFIDENTIAL',30,25)]:
                self.service.platform=platform;self.service.security_level=security
                for grade,rate in [('MASTER',master),('LEGEND',legend)]:
                    self.service.provider_grade=grade
                    self.assertEqual(resolve_commission_rate(self.service,self.profile,None),rate)
                    self.assertEqual(club_provider_specs(self.service,[self.profile,self.profile],10000),[{'commission_type':'PERCENT','commission_rate':rate}]*2)
    def test_pc_top_guard_five_tiers_and_companion_seven_tiers(self):
        self.assertEqual(grade_options('GUARD','PC','TOP_SECRET'),['KING','MASTER','LEGEND','GLORY','STAR'])
        self.service.security_level='TOP_SECRET'
        for grade,rate in [('KING',24),('MASTER',22),('LEGEND',20),('GLORY',18),('STAR',15)]:
            self.service.provider_grade=grade;self.assertEqual(resolve_commission_rate(self.service,None,None),rate)
        self.assertEqual(len(grade_options('COMPANION','MOBILE','ANY')),7)
        self.service.commission_policy='COMPANION';self.service.provider_kind='COMPANION';self.service.security_level='ANY'
        self.assertEqual(resolve_commission_rate(self.service,self.profile,None),20)
    def test_order_freezes_scope_and_commission_when_catalog_or_provider_changes(self):
        self.qualification();order=self.order()
        self.assertEqual(order.provider_requirement_snapshot['commission_rate'],25)
        self.service.platform='MOBILE';self.service.security_level='TOP_SECRET';self.service.provider_grade='LEGEND';self.service.save()
        order.refresh_from_db()
        self.assertTrue(self.profile.can_take_service(order))
        self.assertFalse(self.profile.can_take_service(self.service))
        self.assertEqual(club_provider_specs(order,[self.profile],10000)[0]['commission_rate'],25)
    def test_old_order_empty_snapshot_does_not_gain_new_catalog_requirement(self):
        self.service.provider_grade='';self.service.provider_kind='';self.service.save();order=self.order()
        self.service.provider_grade='MASTER';self.service.provider_kind='GUARD';self.service.save()
        order.refresh_from_db();self.assertIsNone(service_requirement(order));self.assertTrue(self.profile.can_take_service(order))
    def test_higher_skill_package_covers_lower_package_in_same_family(self):
        self.qualification();higher=make_service(game_category=self.game,provider_kind='GUARD',provider_grade='LEGEND',platform='PC',security_level='CONFIDENTIAL',commission_policy='REGULAR',catalog_family='测试护航')
        self.profile.service_items.set([higher]);self.assertEqual(self.profile.order_acceptance_error(self.service),'')
        order=self.order();self.assertTrue(Order.objects.filter(pk=order.pk).filter(qualified_order_q(self.profile),authorized_skill_q(self.profile)).exists())
        self.service.catalog_family='别的玩法';self.assertIn('技能',self.profile.order_acceptance_error(self.service))
    def test_directory_filters_same_qualification_row_and_deduplicates(self):
        self.qualification();self.qualification('MOBILE','TOP_SECRET','MASTER');self.profile.game_categories.add(self.game)
        self.client.force_authenticate(self.boss)
        params={'directory':'1','provider_kind':'GUARD','platform':'MOBILE','security_level':'TOP_SECRET','grade':'LEGEND','game_category':self.game.pk}
        self.assertEqual(self.client.get('/api/users/escorts/',params).data['data'],[])
        params['grade']='MASTER';rows=self.client.get('/api/users/escorts/',params).data['data'];self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['qualifications']),1)
    def test_provider_pool_excludes_wrong_scope_and_low_grade(self):
        self.qualification();self.qualification('MOBILE','TOP_SECRET','MASTER');order=self.order()
        self.client.force_authenticate(self.user)
        rows=self.client.get('/api/orders/orders/',{'status':'pending'}).data['data'];self.assertEqual([r['id'] for r in rows],[order.pk])
        self.service.platform='MOBILE';self.service.security_level='TOP_SECRET';self.service.provider_grade='LEGEND';self.service.save();other=self.order()
        rows=self.client.get('/api/orders/orders/',{'status':'pending'}).data['data'];self.assertNotIn(other.pk,[r['id'] for r in rows])
    def test_admin_rejects_duplicate_or_invalid_scope_and_saves_multiple_qualifications(self):
        rows=[{'game_category':self.game.pk,'provider_kind':'GUARD','platform':'PC','security_level':'CONFIDENTIAL','grade':'LEGEND'}, {'game_category':self.game.pk,'provider_kind':'GUARD','platform':'MOBILE','security_level':'TOP_SECRET','grade':'MASTER'}]
        serializer=AdminEscortSerializer(self.profile,data={'qualifications':rows},partial=True);self.assertTrue(serializer.is_valid(),serializer.errors);serializer.save();self.assertEqual(self.profile.qualifications.count(),2)
        serializer=AdminEscortSerializer(self.profile,data={'qualifications':rows+[rows[0]]},partial=True);self.assertFalse(serializer.is_valid())
        rows[0]['grade']='STAR';serializer=AdminEscortSerializer(self.profile,data={'qualifications':rows},partial=True);self.assertFalse(serializer.is_valid())
    def test_provider_cannot_edit_own_qualifications(self):
        self.client.force_authenticate(self.user);response=self.client.patch('/api/users/escorts/me/',{'qualifications':[]},format='json');self.assertEqual(response.status_code,403)
    def test_service_validation_rejects_unsupported_mobile_guard_tier(self):
        serializer=AdminServiceItemSerializer(self.service,data={'platform':'MOBILE','security_level':'TOP_SECRET','provider_grade':'STAR'},partial=True);self.assertFalse(serializer.is_valid())

    def test_four_notification_switches_are_independent_and_do_not_change_pool(self):
        from users.qualifications import dispatch_notification_enabled
        self.qualification();self.qualification('MOBILE','TOP_SECRET','MASTER');order=self.order()
        self.client.force_authenticate(self.user)
        res=self.client.patch('/api/users/escorts/me/',{'dispatch_notification_preferences':{'PC_CONFIDENTIAL':False,'MOBILE_TOP_SECRET':True}},format='json')
        self.assertEqual(res.data['code'],0);self.profile.refresh_from_db()
        self.assertFalse(dispatch_notification_enabled(self.profile,order))
        rows=self.client.get('/api/orders/orders/',{'status':'pending'}).data['data'];self.assertEqual([r['id'] for r in rows],[order.pk]);self.assertFalse(rows[0]['dispatch_notification_enabled'])
        res=self.client.patch('/api/users/escorts/me/',{'dispatch_notification_preferences':{'PC_TOP_SECRET':True}},format='json');self.assertEqual(res.status_code,400)
        res=self.client.patch('/api/users/escorts/me/',{'dispatch_notification_preferences':{'PC_CONFIDENTIAL':'false'}},format='json');self.assertEqual(res.status_code,400)
    def test_lower_skill_does_not_grant_higher_order_when_qualification_is_high(self):
        self.qualification();self.service.provider_grade='LEGEND';self.service.save()
        low=make_service(game_category=self.game,provider_kind='GUARD',provider_grade='MASTER',platform='PC',security_level='CONFIDENTIAL',catalog_family=self.service.catalog_family)
        self.profile.service_items.set([low]);order=self.order()
        self.assertFalse(Order.objects.filter(pk=order.pk).filter(qualified_order_q(self.profile),authorized_skill_q(self.profile)).exists())

    def test_typed_regular_service_uses_order_rate_even_with_legacy_policy(self):
        self.service.commission_policy='LEGACY';self.service.provider_grade='MASTER'
        self.assertEqual(resolve_commission_rate(self.service,self.profile,None),25)
        self.assertEqual(club_provider_specs(self.service,[self.profile],10000)[0]['commission_rate'],25)
    def test_confirmed_catalog_normalization_preserves_price_images_and_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO
        item=make_service(game_category=self.game,service_type='COMPANION',catalog_family='常规陪玩',catalog_variant='技术 · 单陪1小时',platform='MOBILE',security_level='CONFIDENTIAL',cover_url='https://example.invalid/cover.jpg',price=9000)
        call_command('normalize_provider_qualifications',apply=True,stdout=StringIO());item.refresh_from_db()
        self.assertEqual((item.provider_kind,item.provider_grade,item.security_level,item.price,item.cover_url),('COMPANION','TECHNICAL','ANY',9000,'https://example.invalid/cover.jpg'))
        count=ProviderQualification.objects.count();call_command('normalize_provider_qualifications',apply=True,stdout=StringIO());self.assertEqual(ProviderQualification.objects.count(),count)

    def test_service_shortcuts_exclude_wrong_scope_even_for_recent_recommended_provider(self):
        self.qualification('MOBILE','TOP_SECRET','MASTER')
        self.profile.recommendation_slot=1;self.profile.save(update_fields=['recommendation_slot'])
        self.profile.game_categories.add(self.game)
        old=self.order();old.provider=self.user;old.provider_account=self.account;old.payment_status='PAID'
        old.save(update_fields=['provider','provider_account','payment_status'])
        self.client.force_authenticate(self.boss)
        unscoped=self.client.get('/api/users/escorts/',{'shortcuts':'1'}).data['data']
        self.assertEqual([r['id'] for r in unscoped['recommended']],[self.account.pk])
        self.assertEqual([r['id'] for r in unscoped['recent']],[self.account.pk])
        scoped=self.client.get('/api/users/escorts/',{'shortcuts':'1','service_item':self.service.pk}).data['data']
        self.assertEqual(scoped,{'recent':[],'recommended':[]})
        self.qualification()
        scoped=self.client.get('/api/users/escorts/',{'shortcuts':'1','service_item':self.service.pk}).data['data']
        self.assertEqual([r['id'] for r in scoped['recommended']],[self.account.pk])
        self.assertEqual([r['id'] for r in scoped['recent']],[self.account.pk])

    def test_service_picker_excludes_wrong_scope_and_reads_new_qualification(self):
        self.qualification('MOBILE', 'TOP_SECRET', 'MASTER')
        self.profile.recommendation_slot = 1
        self.profile.save(update_fields=['recommendation_slot'])
        self.client.force_authenticate(self.boss)
        params = {'picker': '1', 'service_item': self.service.pk}
        self.assertEqual(self.client.get('/api/users/escorts/', params).data['data'], [])
        qualification = self.qualification()
        rows = self.client.get('/api/users/escorts/', params).data['data']
        self.assertEqual([row['id'] for row in rows], [self.account.pk])
        self.assertEqual(rows[0]['picker_group'], 'recommended')
        qualification.is_active = False
        qualification.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/users/escorts/', params).data['data'], [])
