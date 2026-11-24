"""Boss-selected teams use purchased headcount and the existing settlement rows."""
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
import os
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import ClubOperationConfig, PointFundingLot
from orders.models import GameCategory, Order, OrderApplication
from orders.tests.factories import make_user, make_provider, make_service, make_wallet, make_escort_level
from site_messages.models import Message
from users.models import EscortProfile, ProviderQualification
from users.test_voice_recordings import audio_file
from wallet.models import ProviderReport, Transaction
from PIL import Image


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class BossSelectedTeamTest(APITestCase):
    def setUp(self):
        media=TemporaryDirectory(prefix='xa-selected-team-test-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.boss = make_user()
        self.boss_account = self.link(self.boss)
        self.wallet = make_wallet(self.boss, balance=100000)
        self.wallet.account = self.boss_account
        self.wallet.save(update_fields=['account'])
        ClubOperationConfig.objects.get_or_create(pk=1)
        PointFundingLot.objects.create(account=self.boss_account, source_key='selected-team-principal',
            kind=PointFundingLot.Kind.PREPAID, remaining=100000)
        self.single = make_service(price=5000, service_type='COMPANION', commission_policy='REGULAR')
        self.double = make_service(price=5000, billing_provider_count=2, fulfillment_provider_count=2, purchase_mode='CONSULT',
                                   service_type='COMPANION', commission_policy='REGULAR', price_unit='局')
        self.providers = [make_provider(gender='FEMALE', deposit_mode='EARNINGS',
                                       pass_tier='BLACK', pass_expires_at=timezone.now()+timedelta(days=1))
                          for _ in range(3)]
        self.accounts = [self.link(user) for user in self.providers]
        for index, user in enumerate(self.providers):
            profile = user.escort_profile
            profile.level = make_escort_level(commission_rate=10+index*10)
            profile.save(update_fields=['level'])
            profile.service_items.add(self.single, self.double)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch',
                     'notify_order_update', '_push_message_safe'):
            mocked = patch('orders.views.'+name)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)
        self.auth(self.boss_account)

    def link(self, user):
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=user).update(account=account)
        return account

    def auth(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)

    def payload(self, **extra):
        payload = {'service_id':self.double.pk, 'provider_ids':[a.pk for a in self.accounts[:2]],
                   'client_request_id':'selected-team-test'}
        payload.update(extra)
        return payload

    def create(self, **extra):
        return self.client.post('/api/orders/create/', self.payload(**extra), format='json')

    def assert_no_payment(self):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertEqual(PointFundingLot.objects.get(account=self.boss_account).remaining, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_quote_and_selected_two_person_order_keep_package_price_and_both_shares(self):
        quote = self.client.post('/api/orders/quote/', self.payload(), format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertEqual(quote.data['data']['amount'], 10000)
        self.assert_no_payment()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.create(expected_amount=10000)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get()
        self.assertEqual((order.amount, order.required_provider_count, order.escort_mode, order.status),
                         (10000, 2, 'DOUBLE', 'GRABBED'))
        self.assertTrue(order.requires_boss_selection)
        self.assertIsNone(order.auto_cancel_at)
        shares = list(order.providers.order_by('pk'))
        self.assertEqual([row.provider_account_id for row in shares], [a.pk for a in self.accounts[:2]])
        self.assertEqual([row.settlement_base for row in shares], [5000,5000])
        self.assertEqual([row.commission_rate for row in shares], [10,20])
        self.assertEqual([row.provider_income for row in shares], [4500,4000])
        self.assertEqual(order.provider_income, 8500)
        self.assertEqual(order.provider_income+order.inviter_commission+order.shop_income, order.amount)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance,90000)
        self.assertEqual(list(Transaction.objects.filter(order=order).values_list('amount',flat=True)),[-10000])
        notices = Message.objects.filter(related_order_id=order.pk)
        self.assertCountEqual(notices.filter(title='老板指定你服务').values_list('recipient_account_id', flat=True),
                              [account.pk for account in self.accounts[:2]])
        self.assertEqual(list(notices.filter(title='订单已安排').values_list('recipient_account_id', flat=True)),
                         [self.boss_account.pk])
        for account in self.accounts[:2]:
            self.auth(account)
            detail = self.client.get(f'/api/orders/orders/{order.pk}/')
            self.assertEqual(detail.data['code'],0,detail.data)
            self.assertEqual(detail.data['data']['amount'],10000)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_fixed_commission_is_deducted_once_and_shared_by_two(self):
        self.double.commission_override=True
        self.double.commission_type='FIXED'
        self.double.commission_fixed=1001
        self.double.save()
        response=self.create()
        self.assertEqual(response.data['code'],0,response.data)
        order=Order.objects.get()
        rows=list(order.providers.order_by('pk'))
        self.assertEqual([row.commission_fixed for row in rows],[501,500])
        self.assertEqual(order.commission_fixed,1001)
        self.assertEqual(order.provider_income,8999)
        self.assertEqual(order.provider_income+order.shop_income+order.inviter_commission,10000)

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_selected_two_person_order_requires_each_members_entry_and_completion(self):
        response=self.create()
        self.assertEqual(response.data['code'],0,response.data)
        order=Order.objects.get()
        def evidence():
            data=BytesIO()
            Image.new('RGB',(2,2),'white').save(data,format='PNG')
            return SimpleUploadedFile('evidence.png',data.getvalue(),content_type='image/png')
        with patch('orders.team_fulfillment.notify_order_update'):
            for index,account in enumerate(self.accounts[:2]):
                self.auth(account)
                started=self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                         {'entry_image':evidence()},format='multipart')
                self.assertEqual(started.data['code'],0,started.data)
                order.refresh_from_db()
                self.assertEqual(order.status,'GRABBED' if index == 0 else 'IN_SERVICE')
            for index,account in enumerate(self.accounts[:2]):
                self.auth(account)
                completed=self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                                           {'completion_image':evidence()},format='multipart')
                self.assertEqual(completed.data['code'],0,completed.data)
                order.refresh_from_db()
                self.assertEqual(order.status,'IN_SERVICE' if index == 0 else 'COMPLETED')
        reports=list(ProviderReport.objects.filter(order=order).order_by('provider_account_id'))
        self.assertEqual([row.amount for row in reports],[5000,5000])
        self.assertTrue(all(row.entry_image and row.completion_image for row in reports))
        self.assertIsNone(order.settled_at)
        self.assertFalse(Transaction.objects.filter(order=order,tx_type='INCOME').exists())

    def test_invalid_counts_duplicates_conflicts_and_shape_reject_before_any_payment(self):
        first, second = [a.pk for a in self.accounts[:2]]
        cases = [
            {'provider_ids':[first]}, {'provider_ids':[first,first]},
            {'provider_ids':[first,second,self.accounts[2].pk]},
            {'provider_ids':[first,second],'provider_id':first},
            {'provider_ids':[],'provider_id':first}, {'provider_ids':'1,2'},
            {'provider_ids':[0,second]}, {'provider_ids':[first,999999]},
            {'service_id':self.single.pk,'provider_ids':[first,second]},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                for path in ('/api/orders/quote/','/api/orders/create/'):
                    response=self.client.post(path,self.payload(**extra),format='json')
                    self.assertEqual(response.data['code'],400,response.data)
                    self.assert_no_payment()

    def test_second_provider_gender_and_qualification_are_checked(self):
        EscortProfile.objects.filter(user=self.providers[1]).update(gender='MALE')
        self.assertEqual(self.create(provider_gender='FEMALE').data['code'],400)
        self.assert_no_payment()
        EscortProfile.objects.filter(user=self.providers[1]).update(gender='FEMALE',is_verified=False)
        self.assertEqual(self.create().data['code'],400)
        self.assert_no_payment()

    def test_second_provider_capacity_rejects_whole_order(self):
        for _ in range(Order.MAX_CONCURRENT_ORDERS):
            Order.objects.create(customer=self.boss,customer_account=self.boss_account,
                                 provider=self.providers[1],provider_account=self.accounts[1],
                                 service=self.single,amount=5000,status='GRABBED')
        response=self.create()
        self.assertEqual(response.data['code'],400,response.data)
        self.assertEqual(Order.objects.count(),3)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance,100000)
        self.assertFalse(Transaction.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_each_selected_member_needs_the_purchased_game_platform_and_grade(self):
        game=GameCategory.objects.create(name='双陪资格测试游戏')
        self.double.game_category=game
        self.double.provider_kind='GUARD'
        self.double.provider_grade='MASTER'
        self.double.platform='PC'
        self.double.security_level='CONFIDENTIAL'
        self.double.save()
        for index,user in enumerate(self.providers[:2]):
            ProviderQualification.objects.create(profile=user.escort_profile,game_category=game,
                provider_kind='GUARD',platform='PC' if index == 0 else 'MOBILE',
                security_level='CONFIDENTIAL',grade='LEGEND')
        for path in ('/api/orders/quote/','/api/orders/create/'):
            response=self.client.post(path,self.payload(),format='json')
            self.assertEqual(response.data['code'],400,response.data)
        self.assert_no_payment()
        ProviderQualification.objects.filter(profile__user=self.providers[1]).update(platform='PC')
        quote=self.client.post('/api/orders/quote/',self.payload(),format='json')
        self.assertEqual(quote.data['code'],0,quote.data)

    def test_same_price_cannot_hide_a_changed_package_headcount_before_debit(self):
        from orders.services import ensure_provider_dispatch_locks

        def change_before_checkout(account_ids):
            ensure_provider_dispatch_locks(account_ids)
            type(self.double).objects.filter(pk=self.double.pk).update(
                price=10000, billing_provider_count=1, fulfillment_provider_count=1)

        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change_before_checkout) as changed:
            response = self.create()
        changed.assert_called_once()
        self.assertEqual(response.data.get('errorCode'), 'SERVICE_CHANGED', response.data)
        self.assert_no_payment()

    def test_same_price_cannot_hide_consultation_requirement_before_debit(self):
        from orders.services import ensure_provider_dispatch_locks

        selected = {'service_id': self.single.pk, 'provider_ids': [self.accounts[0].pk]}
        quote = self.client.post('/api/orders/quote/', self.payload(**selected), format='json')
        self.assertEqual((quote.data['code'], quote.data['data']['amount']), (0, 5000), quote.data)

        def change_before_checkout(account_ids):
            ensure_provider_dispatch_locks(account_ids)
            type(self.single).objects.filter(pk=self.single.pk).update(purchase_mode='CONSULT')

        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change_before_checkout) as changed:
            response = self.create(**selected, expected_amount=5000)
        changed.assert_called_once()
        self.assertEqual(response.data.get('errorCode'), 'SERVICE_CHANGED', response.data)
        self.assert_no_payment()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_selected_profiles_are_rechecked_after_dispatch_locks_before_debit(self):
        from orders.services import lock_provider_dispatch
        def become_unavailable(ids):
            lock_provider_dispatch(ids)
            EscortProfile.objects.filter(user=self.providers[1]).update(status='OFFLINE')
        with patch('orders.views.lock_provider_dispatch',side_effect=become_unavailable) as locked:
            response=self.create()
        self.assertEqual(response.data['code'],400,response.data)
        locked.assert_called_once_with([a.pk for a in self.accounts[:2]])
        self.assert_no_payment()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_empty_double_selection_enters_existing_team_application_flow(self):
        audition = {'dispatch_mode': 'BOSS_SELECT', 'provider_ids': []}
        quote = self.client.post('/api/orders/quote/', self.payload(**audition), format='json')
        self.assertEqual(quote.data['code'],0,quote.data)
        response = self.create(**audition)
        self.assertEqual(response.data['code'],0,response.data)
        order=Order.objects.get()
        self.assertEqual((order.required_provider_count,order.status,order.amount),(2,'PENDING',10000))
        self.assertTrue(order.requires_boss_selection)
        self.assertIsNotNone(order.auto_cancel_at)
        self.assertFalse(order.providers.exists())
        from orders.dispatching import eligibility_error
        self.assertTrue(eligibility_error(order), 'Two-person totals must not be broadcast as one-provider income')
        recordings = []
        for account in self.accounts[:2]:
            self.auth(account)
            uploaded = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '双陪试音',
                'client_request_id': f'selected-audition-{account.pk}'}, format='multipart')
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
            recordings.append(uploaded.data['data']['id'])
        self.auth(self.accounts[0])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/',{'team_size':1},format='json').data['code'],400)
        applied=self.client.post(f'/api/orders/orders/{order.pk}/grab/',
                                 {'team_size':2,'partner_id':self.accounts[1].pk,'recording_id':recordings[0]},format='json')
        self.assertEqual(applied.data['code'],0,applied.data)
        application=OrderApplication.objects.get(order=order)
        self.auth(self.accounts[1])
        confirmed=self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                                   {'action':'CONFIRM_TEAM','recording_id':recordings[1]},format='json')
        self.assertEqual(confirmed.data['code'],0,confirmed.data)
        self.auth(self.boss_account)
        selected=self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/')
        self.assertEqual(selected.data['code'],0,selected.data)
        order.refresh_from_db()
        self.assertEqual((order.escort_mode,order.status),('DOUBLE','GRABBED'))
        self.assertEqual(order.providers.count(),2)
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='PAY').count(),1)

    def test_unavailable_automatic_team_and_single_consultation_are_rejected_before_payment(self):
        with self.settings(GRAB_TEAM_WINDOW_ENABLED=False):
            unavailable = self.create(provider_ids=[], dispatch_mode='AUTO_GRAB')
        self.assertEqual((unavailable.status_code, unavailable.data.get('errorCode')),
                         (503, 'GRAB_WINDOW_UNAVAILABLE'), unavailable.data)
        self.single.purchase_mode='CONSULT'
        self.single.save()
        consult = self.create(service_id=self.single.pk, provider_ids=[], dispatch_mode='BOSS_SELECT')
        self.assertEqual(consult.data['code'], 400, consult.data)
        self.assertIn('联系客服确认方案与报价', consult.data['msg'])
        self.assert_no_payment()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_single_array_and_legacy_scalar_remain_supported(self):
        response=self.create(service_id=self.single.pk,provider_ids=[self.accounts[0].pk])
        self.assertEqual(response.data['code'],0,response.data)
        order=Order.objects.get()
        self.assertEqual((order.amount,order.escort_mode,order.providers.count()),(5000,'SINGLE',1))
        payload={'service_id':self.single.pk,'provider_id':self.accounts[1].pk,'client_request_id':'legacy-scalar'}
        response=self.client.post('/api/orders/create/',payload,format='json')
        self.assertEqual(response.data['code'],0,response.data)
        self.assertEqual(Order.objects.get(pk=response.data['data']['id']).provider_account_id,self.accounts[1].pk)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '按最终联调门禁运行老板选人、团队履约和分账回归')
    def test_selected_team_fund_request_replay_never_debits_twice(self):
        response=self.create()
        self.assertEqual(response.data['code'],0,response.data)
        replay=self.create()
        self.assertEqual(replay.data['code'],409,replay.data)
        self.assertEqual(replay.data['errorCode'],'FUND_ALREADY_PROCESSED')
        self.assertEqual(replay.data['fundResult']['data']['id'],response.data['data']['id'])
        self.assertEqual(Order.objects.count(),1)
        self.assertEqual(Transaction.objects.count(),1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance,90000)
