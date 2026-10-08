import hashlib
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from club_accounts.models import ClubAccount, LegacyAccountMap
from users.models import CustomUser

class Command(BaseCommand):
    help='经历史备份核验后标记旧绑定AppID；默认只读，不清空OpenID或资金'
    def add_arguments(self,parser):
        parser.add_argument('--account-id',type=int,required=True)
        parser.add_argument('--expected-openid-hash',required=True)
        parser.add_argument('--source-appid',required=True)
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        with transaction.atomic():
            a=ClubAccount.objects.select_for_update().get(pk=options['account_id'],account_type='BOSS')
            source=options['source_appid']
            if not source.startswith('wx') or len(source)!=18 or source==settings.WECHAT_MINIAPP_APPID:
                raise CommandError('需提供从历史备份确认的旧AppID')
            if not a.openid or hashlib.sha256(a.openid.encode()).hexdigest()!=options['expected_openid_hash']:
                raise CommandError('绑定已改变，停止处理')
            if not a.is_phone_verified or not a.phone or a.wechat_appid not in ('',source):
                raise CommandError('账号没有已核验手机号或来源冲突')
            mapping=LegacyAccountMap.objects.get(account=a)
            legacy=CustomUser.objects.get(pk=mapping.legacy_user_id)
            if legacy.openid!=a.openid or legacy.phone!=a.phone:
                raise CommandError('历史映射绑定或手机号不一致')
            if options['apply']:
                a.wechat_appid=source;a.save(update_fields=['wechat_appid','updated_at'])
            self.stdout.write(f"账号{a.pk}来源核验通过；"+('已标记旧AppID，等待本人微信手机号授权迁移' if options['apply'] else '只读模式，未修改'))
