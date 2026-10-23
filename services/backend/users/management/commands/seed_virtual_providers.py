import json
import secrets
from datetime import timedelta
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from club_accounts.models import ClubAccount, LegacyAccountMap
from users.models import CustomUser, EscortLevel, EscortProfile
from orders.models import ServiceItem, GameCategory
from wallet.models import Wallet

class Command(BaseCommand):
    help = '创建100个标明虚拟测试的陪玩账号，不充值、不伪造现金押金或购买流水'
    def add_arguments(self, parser):
        parser.add_argument('--output',required=True)
        parser.add_argument('--plan', help='复用已交付的100个账号口令，忽略清单中的本机数据库ID')
    def handle(self,*args,**options):
        output=Path(options['output'])
        if output.exists():
            saved=json.loads(output.read_text(encoding='utf-8'))
            if len(saved)!=100 or not all(ClubAccount.objects.filter(pk=r['id'],username=r['username']).exists() for r in saved):
                raise CommandError('已有清单与账号不一致，拒绝覆盖')
            self.stdout.write('已有100个测试账号，保持账号和密码不变');return
        levels=list(EscortLevel.objects.filter(is_active=True).order_by('sort_order','id'))
        if len(levels)!=5: raise CommandError('需核对五档陪玩等级后再创建')
        if ClubAccount.objects.filter(username__startswith='xa_virtual_').exists() or CustomUser.objects.filter(username__startswith='xa_virtual_').exists():
            raise CommandError('测试账号前缀已存在，拒绝改写账号密码')
        tiers=['BLACK','GOLD','SILVER','BRONZE','']
        expires=timezone.now()+timedelta(days=7)
        services=list(ServiceItem.objects.filter(is_active=True).exclude(service_category__is_gift=True))
        games=list(GameCategory.objects.filter(is_active=True))
        plan=json.loads(Path(options['plan']).read_text(encoding='utf-8')) if options.get('plan') else None
        if plan is not None and (not isinstance(plan,list) or len(plan)!=100 or any(not isinstance(r,dict) or r.get('username')!=f'xa_virtual_{n+1:03d}' or not isinstance(r.get('password'),str) or not 12<=len(r['password'])<=128 for n,r in enumerate(plan))):
            raise CommandError('预生成测试清单不符合100个账号和口令要求')
        rows=[]
        with transaction.atomic():
            for index in range(100):
                level=levels[(index//5)%5];tier=tiers[index%5]
                username=f'xa_virtual_{index+1:03d}'
                password=plan[index]['password'] if plan is not None else secrets.token_urlsafe(12)
                name=f'虚拟测试陪玩{index+1:03d}'
                account=ClubAccount.objects.create_account(username=username,password=password,account_type='PROVIDER',nickname=name)
                user=CustomUser.objects.create_user(username=username,password=password,role='PROVIDER',nickname=name,boss_no=account.account_no)
                LegacyAccountMap.objects.create(legacy_user_id=user.pk,account=account,legacy_role='PROVIDER')
                Wallet.objects.filter(user=user).update(account=account)
                profile=EscortProfile.objects.create(user=user,account=account,display_name=name,level=level,is_verified=True,
                    deposit_mode='EARNINGS',deposit_required=40000,deposit_paid=0,status='AVAILABLE',
                    pass_tier=tier,pass_expires_at=expires if tier else None,
                    bio='虚拟陪玩测试账号，仅用于老板端/陪玩端功能联调，无真实现金押金')
                profile.game_categories.set(games);profile.service_items.set(services)
                rows.append({'id':account.pk,'account_no':account.account_no,'username':username,'password':password,
                    'nickname':name,'level':level.name,'level_rank':level.sort_order,'pass_tier':tier or 'NONE',
                    'pass_expires_at':expires.isoformat() if tier else None,'deposit_mode':'EARNINGS','balance':0})
            # 输出失败时回滚账号创建；清单供本次用户交付，文件权限设为仅当前进程用户。
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');output.chmod(0o600)
        self.stdout.write('成功创建100个虚拟陪玩：25种等级/通行证组合，每种4个；钱包零余额')
