from unittest.mock import patch
from django.test import override_settings
from rest_framework.test import APITestCase
from club_accounts.models import WechatBindingChange
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_user, make_wallet
from wallet.models import Wallet

@override_settings(WECHAT_MOCK_LOGIN=False,WECHAT_MINIAPP_APPID='new-app',WECHAT_MINIAPP_SECRET='secret',LEGACY_FORCE_AUTH_COMPAT=False)
class WechatAppidMigrationTest(APITestCase):
    def setUp(self):
        self.user=make_user(openid='old-openid',phone='13800001111',is_openid_bound=True,is_phone_verified=True)
        self.account=get_or_create_account_for_legacy_user(self.user)
        self.account.wechat_appid='old-app';self.account.save(update_fields=['wechat_appid'])
        make_wallet(self.user,balance=12345)
    def login(self,phone=True):
        with patch('users.views._wechat_code2session',return_value='new-openid'),patch('users.views._wechat_get_phone',return_value='13800001111'):
            return self.client.post('/api/users/wechat-login/',{'code':'fresh',**({'phoneCode':'verified'} if phone else {'checkOnly':True})},format='json')
    def test_verified_phone_migrates_known_previous_app_binding_and_preserves_wallet(self):
        res=self.login();self.assertEqual(res.data['code'],0,res.data)
        self.account.refresh_from_db();self.user.refresh_from_db()
        self.assertEqual(self.account.openid,'new-openid');self.assertEqual(self.account.wechat_appid,'new-app')
        self.assertEqual(self.user.openid,'new-openid');self.assertEqual(Wallet.objects.get(user=self.user).balance,12345)
        self.assertEqual(WechatBindingChange.objects.filter(account=self.account).count(),1)
        self.assertEqual(self.login().data['code'],0);self.assertEqual(WechatBindingChange.objects.count(),1)
    def test_unknown_or_current_app_binding_cannot_be_replaced(self):
        for source in ['', 'new-app']:
            self.account.wechat_appid=source;self.account.save(update_fields=['wechat_appid'])
            self.assertEqual(self.login().data['errorCode'],'ACCOUNT_CONFLICT')
        self.account.refresh_from_db();self.assertEqual(self.account.openid,'old-openid')
        self.assertFalse(WechatBindingChange.objects.exists())
    def test_no_verified_phone_does_not_migrate(self):
        self.assertTrue(self.login(False).data['data']['requiresBinding'])
        self.account.refresh_from_db();self.assertEqual(self.account.openid,'old-openid')
    def test_inconsistent_legacy_binding_rolls_back_migration(self):
        self.user.openid='unrelated';self.user.save(update_fields=['openid'])
        self.assertEqual(self.login().data['errorCode'],'ACCOUNT_CONFLICT')
        self.account.refresh_from_db();self.assertEqual(self.account.openid,'old-openid')
        self.assertFalse(WechatBindingChange.objects.exists())

    def test_source_stamp_is_dry_run_and_requires_exact_verified_binding(self):
        import hashlib
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        self.account.wechat_appid=''
        self.account.save(update_fields=['wechat_appid'])
        args={'account_id':self.account.pk,'expected_openid_hash':hashlib.sha256(b'old-openid').hexdigest(),'source_appid':'wx0000000000000001'}
        call_command('stamp_wechat_binding_source',**args,stdout=StringIO())
        self.account.refresh_from_db()
        self.assertEqual(self.account.wechat_appid,'')
        with self.assertRaises(CommandError):
            call_command('stamp_wechat_binding_source',**{**args,'expected_openid_hash':'wrong'},apply=True,stdout=StringIO())
        call_command('stamp_wechat_binding_source',**args,apply=True,stdout=StringIO())
        self.account.refresh_from_db()
        self.assertEqual(self.account.wechat_appid,args['source_appid'])
        self.assertEqual(self.account.openid,'old-openid')
        self.assertEqual(Wallet.objects.get(user=self.user).balance,12345)
        self.assertEqual(self.login().data['code'],0)
