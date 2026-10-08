import io
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections, close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from users.models import EscortLevel, EscortProfile
from wallet.models import ProviderReport, Wallet, Transaction
from orders.models import Order, OrderApplication
from orders.services import count_active_orders
from orders.tests.factories import make_user, make_provider, make_service, make_order

def image():
    output=io.BytesIO();Image.new('RGB',(4,4),'white').save(output,format='PNG')
    return SimpleUploadedFile('test.png',output.getvalue(),content_type='image/png')

def account(user):
    return get_or_create_account_for_legacy_user(user)

@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class ApplicationTest(APITestCase):
    def setUp(self):
        self.boss=make_user();self.boss_account=account(self.boss)
        self.service=make_service(price=10001,commission_rate=20)
        self.providers=[make_provider() for _ in range(3)]
        self.accounts=[account(user) for user in self.providers]
        for index,(user,a) in enumerate(zip(self.providers,self.accounts)):
            level=EscortLevel.objects.create(name=f'Level{index}',commission_rate=20,sort_order=index+1)
            EscortProfile.objects.filter(user=user).update(account=a,level=level)
        self.order=make_order(self.boss,service=self.service,customer_account=self.boss_account,requires_boss_selection=True)
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now()-timedelta(hours=1))
        self.order.refresh_from_db()
        patcher=patch('orders.applications.notify_order_update');patcher.start();self.addCleanup(patcher.stop)
        media=tempfile.TemporaryDirectory();self.addCleanup(media.cleanup)
        override=override_settings(MEDIA_ROOT=media.name);override.enable();self.addCleanup(override.disable)

    def auth(self,a):
        token,_=issue_account_tokens(a);self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
    def apply(self,index=0,**data):
        self.auth(self.accounts[index])
        return self.client.post(f'/api/orders/orders/{self.order.pk}/grab/',data,format='json')
    def select(self,row):
        self.auth(self.boss_account)
        return self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{row.pk}/select/')
    def action(self,row,a,action):
        self.auth(a)
        return self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{row.pk}/action/',{'action':action},format='json')
    def row(self,index=0):
        return OrderApplication.objects.get(order=self.order,applicant=self.accounts[index])
    def detail(self,a):
        self.auth(a);return self.client.get(f'/api/orders/orders/{self.order.pk}/')

    def test_multiple_people_apply_without_consuming_capacity_or_money(self):
        before=list(Wallet.objects.values_list('balance','frozen_amount'))
        for index in range(3): self.assertEqual(self.apply(index).data['code'],0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status,'PENDING');self.assertIsNone(self.order.provider_account_id)
        self.assertEqual(OrderApplication.objects.filter(order=self.order).count(),3)
        self.assertEqual(count_active_orders(self.accounts[0]),0)
        self.assertEqual(before,list(Wallet.objects.values_list('balance','frozen_amount')))

    def test_boss_ranking_and_selection_leave_other_applicants_unassigned(self):
        for index in range(3): self.apply(index)
        data=self.detail(self.boss_account).data['data']
        self.assertEqual([r['members'][0]['level_rank'] for r in data['applications']],[3,2,1])
        self.assertIn('SELECT_PROVIDER',data['allowed_actions'])
        self.assertEqual(self.select(self.row(1)).data['code'],0)
        self.order.refresh_from_db();self.assertEqual(self.order.provider_account_id,self.accounts[1].pk)
        self.assertEqual(self.order.providers.count(),1)
        self.assertEqual(OrderApplication.objects.filter(status='NOT_SELECTED').count(),2)
        self.assertEqual(self.detail(self.accounts[0]).status_code,404)
        self.assertEqual(self.select(self.row(1)).data['code'],0)
        self.assertEqual(self.select(self.row(0)).data['code'],400)

    def test_double_requires_actual_partner_consent_and_conserves_odd_amount(self):
        self.assertEqual(self.apply(team_size=2,partner_id=self.accounts[1].pk).data['code'],0)
        row=self.row()
        self.assertEqual(self.select(row).data['code'],400)
        self.assertEqual(self.action(row,self.accounts[0],'CONFIRM_TEAM').data['code'],400)
        self.assertEqual(self.action(row,self.accounts[1],'CONFIRM_TEAM').data['code'],0)
        self.assertEqual(self.select(row).data['code'],0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.escort_mode,'DOUBLE')
        shares=list(self.order.providers.all())
        self.assertEqual(sum(s.settlement_base for s in shares),10001)
        self.assertEqual(sum(s.provider_income for s in shares)+self.order.inviter_commission+self.order.shop_income,10001)
        self.assertEqual([count_active_orders(a) for a in self.accounts],[1,1,0])
        self.assertIn('START',self.detail(self.accounts[1]).data['data']['allowed_actions'])

    def test_double_group_sorted_by_its_lower_level(self):
        self.apply(team_size=2,partner_id=self.accounts[2].pk);self.apply(1)
        rows=self.detail(self.boss_account).data['data']['applications']
        self.assertEqual(rows[0]['members'][0]['level_rank'],2)
        self.assertEqual(rows[1]['team_size'],2)

    def test_no_applicant_can_read_other_candidates(self):
        self.apply();self.apply(1)
        self.auth(self.accounts[0])
        data=self.client.get('/api/orders/orders/?status=pending').data['data'][0]
        self.assertEqual(data['applications'],[])
        self.assertEqual(len(data['my_applications']),1)

    def test_withdraw_and_reapply_reset_partner_consent(self):
        self.apply(team_size=2,partner_id=self.accounts[1].pk);row=self.row()
        self.action(row,self.accounts[1],'CONFIRM_TEAM')
        self.action(row,self.accounts[1],'WITHDRAW')
        self.assertEqual(self.select(row).data['code'],400)
        self.apply(team_size=2,partner_id=self.accounts[1].pk);row.refresh_from_db()
        self.assertFalse(row.partner_confirmed)
        self.assertEqual(self.apply().data['code'],400)

    def test_disabled_partner_or_full_capacity_at_selection_roll_back_everything(self):
        self.apply(team_size=2,partner_id=self.accounts[1].pk);row=self.row()
        self.action(row,self.accounts[1],'CONFIRM_TEAM')
        EscortProfile.objects.filter(account=self.accounts[1]).update(status='OFFLINE')
        self.assertEqual(self.select(row).data['code'],400)
        self.assertFalse(self.order.providers.exists());row.refresh_from_db();self.assertEqual(row.status,'PENDING')
        EscortProfile.objects.filter(account=self.accounts[1]).update(status='AVAILABLE')
        make_order(self.boss,service=self.service,provider=self.providers[1],provider_account=self.accounts[1],status='GRABBED')
        with patch.object(Order,'MAX_CONCURRENT_ORDERS',1):
            self.assertEqual(self.select(row).data['code'],400)
        self.order.refresh_from_db();self.assertEqual(self.order.status,'PENDING')

    def test_pass_delay_applies_to_both_members(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now())
        self.assertEqual(self.apply().data['code'],400)
        for a in self.accounts:
            EscortProfile.objects.filter(account=a).update(pass_tier='BLACK',pass_expires_at=timezone.now()+timedelta(days=1))
        self.assertEqual(self.apply(team_size=2,partner_id=self.accounts[1].pk).data['code'],0)

    def test_invalid_group_inputs_and_wrong_boss_are_rejected(self):
        for data in [{'team_size':True},{'team_size':3},{'team_size':2},{'team_size':2,'partner_id':self.accounts[0].pk},{'team_size':1,'partner_id':self.accounts[1].pk}]:
            self.assertEqual(self.apply(**data).data['code'],400,data)
        self.apply();self.auth(account(make_user()))
        self.assertEqual(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{self.row().pk}/select/').data['code'],403)

    def test_cancelled_or_expired_order_rejects_application_and_selection(self):
        self.apply();row=self.row()
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.select(row).data['code'],400)
        self.assertEqual(self.apply(1).data['code'],400)

    def test_two_members_submit_evidence_before_progress_and_reports_use_their_shares(self):
        self.apply(team_size=2,partner_id=self.accounts[1].pk);row=self.row()
        self.action(row,self.accounts[1],'CONFIRM_TEAM');self.select(row)
        for phase,field,before,after in [('start-service','entry_image','GRABBED','IN_SERVICE'),('complete','completion_image','IN_SERVICE','COMPLETED')]:
            for index in (0,1):
                self.auth(self.accounts[index])
                res=self.client.post(f'/api/orders/orders/{self.order.pk}/{phase}/',{field:image()},format='multipart')
                self.assertEqual(res.data['code'],0,res.data)
                self.order.refresh_from_db();self.assertEqual(self.order.status,before if index==0 else after)
        for report in ProviderReport.objects.filter(order=self.order):
            self.assertEqual(report.amount,self.order.providers.get(provider=report.provider).settlement_base)
            self.assertTrue(report.entry_image);self.assertTrue(report.completion_image)
        self.assertIsNone(self.order.settled_at)
        self.assertFalse(Transaction.objects.filter(order=self.order,tx_type='INCOME').exists())

    def test_seed_creates_all_25_combinations_and_keeps_passwords_on_rerun(self):
        EscortLevel.objects.all().delete()
        for n,name in enumerate(['大师','王者','传奇','荣耀','明星']):
            EscortLevel.objects.create(name=name,sort_order=n+1,commission_rate=20)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'accounts.json'
            plan=Path(folder)/'plan.json'
            plan.write_text('[]',encoding='utf-8')
            from django.core.management.base import CommandError
            with self.assertRaises(CommandError):
                call_command('seed_virtual_providers',output=str(output),plan=str(plan))
            expected=[{'username':f'xa_virtual_{n+1:03d}','password':f'local-test-pass-{n+1:03d}'} for n in range(100)]
            plan.write_text(json.dumps(expected),encoding='utf-8')
            call_command('seed_virtual_providers',output=str(output),plan=str(plan))
            rows=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(len(rows),100)
            self.assertEqual([r['password'] for r in rows],[r['password'] for r in expected])
            combos={}
            for row in rows:
                combos[row['level'],row['pass_tier']]=combos.get((row['level'],row['pass_tier']),0)+1
            self.assertEqual(len(combos),25);self.assertEqual(set(combos.values()),{4})
            a=ClubAccount.objects.get(pk=rows[0]['id']);self.assertTrue(a.check_password(rows[0]['password']))
            self.assertEqual(Wallet.objects.filter(account__username__startswith='xa_virtual_').count(),100)
            self.assertFalse(Wallet.objects.filter(account__username__startswith='xa_virtual_').exclude(balance=0).exists())
            self.assertEqual(EscortProfile.objects.filter(account__username__startswith='xa_virtual_',deposit_mode='EARNINGS',deposit_paid=0).count(),100)
            content=output.read_text(encoding='utf-8');call_command('seed_virtual_providers',output=str(output));self.assertEqual(content,output.read_text(encoding='utf-8'))

@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class ApplicationConcurrencyTest(TransactionTestCase):
    def setUp(self):
        self.boss=make_user();self.boss_account=account(self.boss);self.service=make_service()
        self.users=[make_provider() for _ in range(2)];self.accounts=[account(u) for u in self.users]
        for u,a in zip(self.users,self.accounts):EscortProfile.objects.filter(user=u).update(account=a)
        self.order=self.new_order()
    def new_order(self):
        o=make_order(self.boss,service=self.service,customer_account=self.boss_account,requires_boss_selection=True)
        Order.objects.filter(pk=o.pk).update(created_at=timezone.now()-timedelta(hours=1))
        return o
    def race(self,targets,token):
        barrier=threading.Barrier(2)
        def post(path):
            close_old_connections()
            try:
                c=APIClient();c.credentials(HTTP_AUTHORIZATION='Bearer '+token);barrier.wait(timeout=5)
                return c.post(path,{},format='json').data
            finally: connections.close_all()
        with patch('orders.applications.notify_order_update'),ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(post,path) for path in targets]
            return [job.result(timeout=15) for job in jobs]
    def test_two_boss_selections_cannot_assign_the_same_order_twice(self):
        rows=[OrderApplication.objects.create(order=self.order,applicant=a,partner_confirmed=True) for a in self.accounts]
        token,_=issue_account_tokens(self.boss_account)
        result=self.race([f'/api/orders/orders/{self.order.pk}/applications/{r.pk}/select/' for r in rows],token)
        self.assertEqual(sum(r['code']==0 for r in result),1,result)
        self.assertEqual(self.order.providers.count(),1)
    def test_two_orders_compete_for_last_slot(self):
        second=self.new_order()
        rows=[OrderApplication.objects.create(order=o,applicant=self.accounts[0],partner_confirmed=True) for o in [self.order,second]]
        token,_=issue_account_tokens(self.boss_account)
        with patch.object(Order,'MAX_CONCURRENT_ORDERS',1):
            result=self.race([f'/api/orders/orders/{o.pk}/applications/{r.pk}/select/' for o,r in zip([self.order,second],rows)],token)
        self.assertEqual(sum(r['code']==0 for r in result),1,result)
        self.assertEqual(count_active_orders(self.accounts[0]),1)
