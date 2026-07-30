from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from club_accounts.models import LegacyAccountMap
from audition.models import AuditionLink, AuditionSignup
from chat.models import ChatMessage, ChatSession
from console.models import AdminAuditLog, AdminMembership
from coupons.models import UserCoupon
from orders.models import (
    Evaluation,
    Order,
    OrderProvider,
    OrderStatusLog,
    ServiceFavorite,
)
from site_messages.models import Message
from users.models import (
    CheckinMonthProgress,
    CheckinRecord,
    CustomerGameProfile,
    EscortProfile,
    EscortSchedule,
    ProviderPassPurchase,
)
from wallet.models import (
    DisposeRecord,
    ProviderReport,
    RechargeRecord,
    Transaction,
    Wallet,
    WithdrawRequest,
)


RELATIONS = (
    ('后台成员', AdminMembership, 'user_id', 'account_id'),
    ('后台审计', AdminAuditLog, 'operator_id', 'operator_account_id'),
    ('老板游戏资料', CustomerGameProfile, 'user_id', 'account_id'),
    ('陪玩档案', EscortProfile, 'user_id', 'account_id'),
    ('通行证', ProviderPassPurchase, 'provider_id', 'account_id'),
    ('签到', CheckinRecord, 'user_id', 'account_id'),
    ('签到月份', CheckinMonthProgress, 'user_id', 'account_id'),
    ('陪玩档期', EscortSchedule, 'provider_id', 'account_id'),
    ('服务收藏', ServiceFavorite, 'user_id', 'account_id'),
    ('订单老板', Order, 'customer_id', 'customer_account_id'),
    ('订单陪玩', Order, 'provider_id', 'provider_account_id'),
    ('订单推荐人', Order, 'inviter_id', 'inviter_account_id'),
    ('订单陪玩明细', OrderProvider, 'provider_id', 'provider_account_id'),
    ('订单日志操作人', OrderStatusLog, 'operator_id', 'operator_account_id'),
    ('评价老板', Evaluation, 'customer_id', 'customer_account_id'),
    ('评价陪玩', Evaluation, 'provider_id', 'provider_account_id'),
    ('钱包', Wallet, 'user_id', 'account_id'),
    ('流水操作人', Transaction, 'operator_id', 'operator_account_id'),
    ('报单陪玩', ProviderReport, 'provider_id', 'provider_account_id'),
    ('报单审核人', ProviderReport, 'auditor_id', 'auditor_account_id'),
    ('提现账户', WithdrawRequest, 'user_id', 'account_id'),
    ('提现审核人', WithdrawRequest, 'auditor_id', 'auditor_account_id'),
    ('充值账户', RechargeRecord, 'user_id', 'account_id'),
    ('充值操作人', RechargeRecord, 'operator_id', 'operator_account_id'),
    ('奖罚账户', DisposeRecord, 'user_id', 'account_id'),
    ('奖罚操作人', DisposeRecord, 'operator_id', 'operator_account_id'),
    ('客服会话', ChatSession, 'user_id', 'account_id'),
    ('客服消息发送人', ChatMessage, 'sender_id', 'sender_account_id'),
    ('用户优惠券', UserCoupon, 'user_id', 'account_id'),
    ('站内消息', Message, 'recipient_id', 'recipient_account_id'),
    ('试音创建人', AuditionLink, 'operator_id', 'operator_account_id'),
    ('试音老板', AuditionLink, 'boss_user_id', 'boss_account_id'),
    ('试音陪玩', AuditionLink, 'provider_user_id', 'provider_account_id'),
    ('试音报名人', AuditionSignup, 'applicant_id', 'applicant_account_id'),
    ('试音审核人', AuditionSignup, 'auditor_id', 'auditor_account_id'),
)


class Command(BaseCommand):
    help = '验证所有业务外键的新 ClubAccount 关系及核心资金汇总'

    def handle(self, *args, **options):
        mappings = dict(
            LegacyAccountMap.objects.values_list('legacy_user_id', 'account_id')
        )
        errors = []
        checked = 0

        for label, model, legacy_field, account_field in RELATIONS:
            for row in model.objects.exclude(**{legacy_field: None}).only(
                'id', legacy_field, account_field,
            ).iterator():
                checked += 1
                legacy_id = getattr(row, legacy_field)
                actual_account_id = getattr(row, account_field)
                expected_account_id = mappings.get(legacy_id)
                if actual_account_id != expected_account_id:
                    errors.append(
                        f'{label}#{row.pk}: {actual_account_id} != {expected_account_id}'
                    )

        wallet_totals = Wallet.objects.aggregate(
            balance=Sum('balance'),
            frozen=Sum('frozen_amount'),
        )
        order_total = Order.objects.aggregate(total=Sum('amount'))['total'] or 0
        transaction_total = Transaction.objects.aggregate(total=Sum('amount'))['total'] or 0

        if errors:
            preview = '\n'.join(errors[:20])
            raise CommandError(
                f'业务关系校验失败，共 {len(errors)} 项：\n{preview}'
            )

        self.stdout.write(self.style.SUCCESS(
            '业务关系校验通过：'
            f'{checked} 条关系；订单金额 {order_total}；'
            f'钱包余额 {wallet_totals["balance"] or 0}；'
            f'冻结金额 {wallet_totals["frozen"] or 0}；'
            f'流水净额 {transaction_total}'
        ))
