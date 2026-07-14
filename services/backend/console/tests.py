"""console 后台接口的单元测试：评价管理 / 报单审核 / 提现审核 / 代派单 / 试音链接 / 客服信息。"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from audition.models import AuditionLink
from console.models import AdminMembership, AdminRole
from orders.models import Evaluation
from orders.ratings import apply_escort_rating
from orders.tests.factories import (
    Transaction,
    make_boss_type,
    make_completed_order,
    make_console_user,
    make_escort_level,
    make_evaluation,
    make_order,
    make_promotion,
    make_provider,
    make_service,
    make_service_category,
    make_superuser,
    make_user,
    make_wallet,
)
from orders.models import Order, OrderProvider, OrderStatusLog
from promotions.models import Promotion
from users.models import EscortLevel, EscortProfile
from wallet.models import (
    COMMISSION_RATE_KEY,
    DisposeRecord,
    ProviderReport,
    RechargeRecord,
    SystemConfig,
    Wallet,
    WithdrawRequest,
)


class EvaluationAdminTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user(nickname='张三')
        self.provider = make_provider()
        self.order = make_completed_order(self.customer, self.provider)

    def _eval(self, **kwargs):
        return make_evaluation(self.order, **kwargs)

    def test_list_returns_evaluations(self):
        self._eval(score=5)
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/evaluations/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 1)

    def test_list_filter_by_score(self):
        self._eval(score=5)
        c2 = make_user()
        o2 = make_completed_order(c2, self.provider)
        make_evaluation(o2, score=2)
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/evaluations/?score=2')
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['score'], 2)

    def test_list_filter_only_unreplied(self):
        self._eval(score=5, reply_content='已回复')
        c2 = make_user()
        o2 = make_completed_order(c2, self.provider)
        make_evaluation(o2, score=4)
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/evaluations/?only_unreplied=1')
        self.assertEqual(res.data['data']['total'], 1)

    def test_reply_success(self):
        ev = self._eval(score=5)
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/evaluations/{ev.id}/reply/', {'reply_content': '感谢'})
        self.assertEqual(res.data['code'], 0)
        ev.refresh_from_db()
        self.assertEqual(ev.reply_content, '感谢')
        self.assertIsNotNone(ev.replied_at)

    def test_reply_rejects_empty(self):
        ev = self._eval(score=5)
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/evaluations/{ev.id}/reply/', {'reply_content': '  '})
        self.assertEqual(res.data['code'], 400)

    def test_destroy_rolls_back_rating(self):
        apply_escort_rating(self.provider.id, 4)  # 模拟评价已计入聚合
        ev = self._eval(score=4)
        self.client.force_authenticate(self.admin)
        res = self.client.delete(f'/api/admin/evaluations/{ev.id}/')
        self.assertEqual(res.data['code'], 0)
        self.assertFalse(Evaluation.objects.filter(id=ev.id).exists())
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.rating_count, 0)
        self.assertEqual(self.provider.escort_profile.rating_avg, Decimal('0.00'))

    def test_non_console_user_denied(self):
        ev = self._eval(score=5)
        self.client.force_authenticate(self.customer)
        res = self.client.get('/api/admin/evaluations/')
        self.assertEqual(res.status_code, 403)

    def test_perm_view_allows_list_but_delete_denied(self):
        ev = self._eval(score=5)
        user = make_console_user(perms=['evaluation:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/admin/evaluations/').status_code, 200)
        res = self.client.delete(f'/api/admin/evaluations/{ev.id}/')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Evaluation.objects.filter(id=ev.id).exists())

    def test_perm_delete_allows_destroy(self):
        ev = self._eval(score=5)
        user = make_console_user(perms=['evaluation:view', 'evaluation:delete'])
        self.client.force_authenticate(user)
        res = self.client.delete(f'/api/admin/evaluations/{ev.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Evaluation.objects.filter(id=ev.id).exists())


class ProviderReportApproveTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider()

    def _report(self, amount=10000):
        return ProviderReport.objects.create(
            provider=self.provider, game_name='王者', amount=amount
        )

    def test_approve_credits_with_commission(self):
        # 默认抽成率 20% -> 实得 80%
        report = self._report(amount=10000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')
        self.assertEqual(res.data['code'], 0)
        report.refresh_from_db()
        self.assertEqual(report.status, ProviderReport.Status.APPROVED)
        self.assertEqual(report.commission_rate, 20)
        self.assertEqual(report.payout_amount, 8000)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
        tx = Transaction.objects.get(wallet=self.provider.wallet)
        self.assertEqual(tx.tx_type, Transaction.TxType.INCOME)
        self.assertEqual(tx.amount, 8000)

    def test_order_report_approve_does_not_credit_twice(self):
        customer = make_user()
        order = make_completed_order(
            customer, self.provider, amount=10000, provider_income=8000,
        )
        report = ProviderReport.objects.create(
            provider=self.provider, order=order, game_name='王者', amount=10000,
        )
        balance_before = self.provider.wallet.balance
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')
        self.assertEqual(res.data['code'], 0)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, balance_before)
        report.refresh_from_db()
        self.assertEqual(report.payout_amount, 8000)

    def test_approve_uses_configured_rate(self):
        SystemConfig.objects.create(key=COMMISSION_RATE_KEY, value='30')
        report = self._report(amount=10000)
        self.client.force_authenticate(self.admin)
        self.client.post(f'/api/admin/reports/{report.id}/approve/')
        report.refresh_from_db()
        self.assertEqual(report.commission_rate, 30)
        self.assertEqual(report.payout_amount, 7000)

    def test_pending_report_returns_suggested_level_commission(self):
        level = make_escort_level(name='钻石', commission_rate=12)
        self.provider.escort_profile.level = level
        self.provider.escort_profile.save(update_fields=['level'])
        report = self._report(amount=10000)
        self.client.force_authenticate(self.admin)

        res = self.client.get('/api/admin/reports/')

        row = res.data['data']['list'][0]
        self.assertEqual(row['id'], report.id)
        self.assertEqual(row['suggested_commission_rate'], 12)
        self.assertEqual(row['commission_source_label'], '等级抽成（钻石）')

    def test_approve_prefers_provider_level_rate(self):
        SystemConfig.objects.create(key=COMMISSION_RATE_KEY, value='30')
        level = make_escort_level(name='钻石', commission_rate=12)
        self.provider.escort_profile.level = level
        self.provider.escort_profile.save(update_fields=['level'])
        report = self._report(amount=10000)
        self.client.force_authenticate(self.admin)

        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')

        self.assertEqual(res.data['code'], 0)
        report.refresh_from_db()
        self.assertEqual(report.commission_rate, 12)
        self.assertEqual(report.payout_amount, 8800)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8800)
        tx = Transaction.objects.get(wallet=self.provider.wallet)
        self.assertIn('等级抽成（钻石）', tx.remark)

    def test_approve_twice_rejected(self):
        report = self._report()
        self.client.force_authenticate(self.admin)
        self.client.post(f'/api/admin/reports/{report.id}/approve/')
        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')
        self.assertEqual(res.data['code'], 400)


class WithdrawAdminTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider()
        make_wallet(self.provider, balance=50000)

    def _apply_withdraw(self, amount=20000):
        """通过 C 端接口申请提现，得到冻结状态的提现单。"""
        self.client.force_authenticate(self.provider)
        res = self.client.post('/api/wallet/withdraw/', {
            'amount': amount,
            'payee_method': 'WECHAT',
            'payee_account': 'wx123',
            'payee_name': '李四',
        })
        self.assertEqual(res.data['code'], 0)
        return WithdrawRequest.objects.get(user=self.provider)

    def test_apply_freezes_balance(self):
        self._apply_withdraw(amount=20000)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 30000)
        self.assertEqual(wallet.frozen_amount, 20000)

    def test_approve_deducts_frozen(self):
        withdraw = self._apply_withdraw(amount=20000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/withdrawals/{withdraw.id}/approve/',
            {'payout_reference': 'WX-PAYOUT-001'},
        )
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 30000)
        self.assertEqual(wallet.frozen_amount, 0)
        withdraw.refresh_from_db()
        self.assertEqual(withdraw.status, WithdrawRequest.Status.APPROVED)
        self.assertEqual(withdraw.payout_reference, 'WX-PAYOUT-001')
        self.assertIsNotNone(withdraw.paid_at)

    def test_reject_refunds_balance(self):
        withdraw = self._apply_withdraw(amount=20000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/withdrawals/{withdraw.id}/reject/',
            {'audit_remark': '信息有误'},
        )
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 50000)
        self.assertEqual(wallet.frozen_amount, 0)
        withdraw.refresh_from_db()
        self.assertEqual(withdraw.status, WithdrawRequest.Status.REJECTED)

    def test_reject_requires_remark(self):
        withdraw = self._apply_withdraw(amount=20000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/withdrawals/{withdraw.id}/reject/', {})
        self.assertEqual(res.data['code'], 400)

    def test_approve_twice_rejected(self):
        withdraw = self._apply_withdraw(amount=20000)
        self.client.force_authenticate(self.admin)
        self.client.post(
            f'/api/admin/withdrawals/{withdraw.id}/approve/',
            {'payout_reference': 'WX-PAYOUT-002'},
        )
        res = self.client.post(
            f'/api/admin/withdrawals/{withdraw.id}/approve/',
            {'payout_reference': 'WX-PAYOUT-003'},
        )
        self.assertEqual(res.data['code'], 400)

    def test_approve_requires_payout_reference(self):
        withdraw = self._apply_withdraw(amount=20000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/withdrawals/{withdraw.id}/approve/', {})
        self.assertEqual(res.data['code'], 400)


class RechargeAdminTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user()
        make_wallet(self.boss, balance=0)

    def test_recharge_without_gift(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/wallets/recharge/', {
            'user_id': self.boss.id,
            'amount': 10000,
        })
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['credited_coins'], 1000)
        self.assertEqual(res.data['data']['exchange_rate'], 10)
        self.assertIn('入账兴安币', res.data['msg'])
        wallet = Wallet.objects.get(user=self.boss)
        self.assertEqual(wallet.balance, 10000)
        self.assertEqual(wallet.total_recharge, 10000)
        self.assertEqual(wallet.total_gift, 0)
        self.assertEqual(RechargeRecord.objects.filter(user=self.boss).count(), 1)
        self.assertEqual(
            Transaction.objects.filter(wallet=wallet, tx_type=Transaction.TxType.TOPUP).count(), 1
        )

    def test_recharge_with_gift(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/wallets/recharge/', {
            'user_id': self.boss.id,
            'amount': 10000,
            'gift_amount': 2000,
            'remark': '充1万送2千',
        })
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.boss)
        self.assertEqual(wallet.balance, 12000)
        self.assertEqual(wallet.total_recharge, 10000)
        self.assertEqual(wallet.total_gift, 2000)
        record = RechargeRecord.objects.get(user=self.boss)
        self.assertEqual(record.total_amount, 12000)
        self.assertEqual(record.operator_id, self.admin.id)
        self.assertEqual(
            Transaction.objects.filter(wallet=wallet, tx_type=Transaction.TxType.GIFT).count(), 1
        )

    def test_recharge_rejects_zero_amount(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/wallets/recharge/', {
            'user_id': self.boss.id,
            'amount': 0,
        })
        self.assertEqual(res.data['code'], 400)
        self.assertFalse(RechargeRecord.objects.exists())

    def test_recharge_rejects_unknown_user(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/wallets/recharge/', {
            'user_id': 999999,
            'amount': 10000,
        })
        self.assertEqual(res.data['code'], 404)

    def test_recharge_requires_permission(self):
        user = make_console_user(perms=['wallet:view'])
        self.client.force_authenticate(user)
        res = self.client.post('/api/admin/wallets/recharge/', {
            'user_id': self.boss.id,
            'amount': 10000,
        })
        self.assertEqual(res.status_code, 403)

    def test_recharge_records_list(self):
        self.client.force_authenticate(self.admin)
        self.client.post('/api/admin/wallets/recharge/', {
            'user_id': self.boss.id,
            'amount': 10000,
            'gift_amount': 1000,
        })
        res = self.client.get('/api/admin/recharge-records/')
        self.assertEqual(res.data['code'], 0)
        rows = res.data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['gift_amount'], 1000)


class DisposeAdminTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider()
        make_wallet(self.provider, balance=10000)

    @property
    def _escort_id(self):
        return self.provider.escort_profile.id

    def test_reward_increases_balance(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'REWARD',
            'amount': 3000,
            'reason': '优质服务',
        })
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 13000)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.total_reward, 3000)
        record = DisposeRecord.objects.get(user=self.provider)
        self.assertEqual(record.dispose_type, DisposeRecord.DisposeType.REWARD)
        self.assertEqual(record.operator_id, self.admin.id)
        self.assertEqual(
            Transaction.objects.filter(
                wallet=wallet, tx_type=Transaction.TxType.REWARD
            ).count(), 1
        )

    def test_penalty_decreases_balance(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'PENALTY',
            'amount': 4000,
            'reason': '迟到',
        })
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 6000)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.total_penalty, 4000)
        self.assertEqual(
            Transaction.objects.filter(
                wallet=wallet, tx_type=Transaction.TxType.PENALTY
            ).count(), 1
        )

    def test_penalty_rejected_when_balance_insufficient(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'PENALTY',
            'amount': 20000,
        })
        self.assertEqual(res.data['code'], 400)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 10000)
        self.assertFalse(DisposeRecord.objects.exists())

    def test_dispose_rejects_invalid_type(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'FOO',
            'amount': 1000,
        })
        self.assertEqual(res.data['code'], 400)

    def test_dispose_requires_permission(self):
        user = make_console_user(perms=['escort:view'])
        self.client.force_authenticate(user)
        res = self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'REWARD',
            'amount': 1000,
        })
        self.assertEqual(res.status_code, 403)

    def test_dispose_records_list_filter(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'REWARD', 'amount': 1000,
        })
        self.client.post(f'/api/admin/escorts/{self._escort_id}/dispose/', {
            'dispose_type': 'PENALTY', 'amount': 500,
        })
        res = self.client.get('/api/admin/dispose-records/?dispose_type=REWARD')
        self.assertEqual(res.data['code'], 0)
        rows = res.data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['dispose_type'], 'REWARD')


class BossTypeAdminTest(APITestCase):
    """老板分级 CRUD + 鉴权。"""

    def setUp(self):
        self.admin = make_superuser()

    def test_create_boss_type(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/boss-types/', {
            'name': 'VIP',
            'discount_rate': 90,
            'remark': '九折',
        })
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['discount_rate'], 90)

    def test_list_boss_types(self):
        make_boss_type(name='普通', discount_rate=100)
        make_boss_type(name='钻石', discount_rate=80)
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/boss-types/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 2)

    def test_update_boss_type(self):
        bt = make_boss_type(name='白银', discount_rate=95)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'/api/admin/boss-types/{bt.id}/', {
            'discount_rate': 85,
        })
        self.assertEqual(res.data['code'], 0)
        bt.refresh_from_db()
        self.assertEqual(bt.discount_rate, 85)

    def test_create_requires_permission(self):
        user = make_console_user(perms=['boss_type:view'])
        self.client.force_authenticate(user)
        res = self.client.post('/api/admin/boss-types/', {
            'name': 'X', 'discount_rate': 90,
        })
        self.assertEqual(res.status_code, 403)


class UserBossFieldsAdminTest(APITestCase):
    """后台用户序列化器暴露推荐人 / 老板分级字段。"""

    def setUp(self):
        self.admin = make_superuser()

    def test_user_serializer_exposes_boss_fields(self):
        boss_type = make_boss_type(name='VIP', discount_rate=90)
        inviter = make_user(nickname='推荐人甲')
        boss = make_user(nickname='老板乙')
        boss.inviter = inviter
        boss.inviter_commission_rate = 30
        boss.boss_type = boss_type
        boss.boss_no = 'B001'
        boss.save()
        self.client.force_authenticate(self.admin)
        res = self.client.get(f'/api/admin/users/{boss.id}/')
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['inviter'], inviter.id)
        self.assertEqual(data['inviter_name'], '推荐人甲')
        self.assertEqual(data['inviter_commission_rate'], 30)
        self.assertEqual(data['boss_type_name'], 'VIP')
        self.assertEqual(data['boss_no'], 'B001')

    def test_update_user_boss_fields(self):
        boss_type = make_boss_type(name='钻石', discount_rate=80)
        inviter = make_user()
        boss = make_user()
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'/api/admin/users/{boss.id}/', {
            'inviter': inviter.id,
            'inviter_commission_rate': 20,
            'boss_type': boss_type.id,
            'boss_no': 'B999',
        })
        self.assertEqual(res.data['code'], 0)
        boss.refresh_from_db()
        self.assertEqual(boss.inviter_id, inviter.id)
        self.assertEqual(boss.inviter_commission_rate, 20)
        self.assertEqual(boss.boss_type_id, boss_type.id)
        self.assertEqual(boss.boss_no, 'B999')


class PlayerDashboardTest(APITestCase):
    """陪玩数据看板聚合接口：KPI 男女拆分 / 二次 KPI / 排行榜 / 鉴权。"""

    URL = '/api/admin/dashboard/player'

    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        self.normal_cat = make_service_category(is_gift=False)
        self.gift_cat = make_service_category(is_gift=True)
        self.game = make_service(name='上分', price=10000, service_category=self.normal_cat)
        self.gift = make_service(name='礼物', price=5000, service_category=self.gift_cat)
        self.male = make_provider(gender=EscortProfile.Gender.MALE)
        self.female = make_provider(gender=EscortProfile.Gender.FEMALE)

    def _paid_order(self, provider, service, amount, provider_income=0):
        return make_order(
            self.customer, service=service, provider=provider,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            amount=amount, provider_income=provider_income,
        )

    def test_kpi_splits_by_gender_and_category(self):
        self._paid_order(self.male, self.game, 10000)
        self._paid_order(self.female, self.game, 8000)
        self._paid_order(self.male, self.gift, 5000)
        self._paid_order(self.female, self.gift, 3000)
        self.client.force_authenticate(self.admin)
        kpi = self.client.get(self.URL).data['data']['kpi']
        self.assertEqual(kpi['total_amount'], 26000)
        self.assertEqual(kpi['game_amount'], 18000)
        self.assertEqual(kpi['gift_amount'], 8000)
        self.assertEqual(kpi['male_game'], 10000)
        self.assertEqual(kpi['female_game'], 8000)
        self.assertEqual(kpi['male_gift'], 5000)
        self.assertEqual(kpi['female_gift'], 3000)

    def test_unpaid_orders_excluded(self):
        self._paid_order(self.male, self.game, 10000)
        make_order(
            self.customer, service=self.game, provider=self.female,
            status=Order.Status.PENDING, payment_status=Order.PaymentStatus.UNPAID,
            amount=9999,
        )
        self.client.force_authenticate(self.admin)
        kpi = self.client.get(self.URL).data['data']['kpi']
        self.assertEqual(kpi['total_amount'], 10000)

    def test_date_filter(self):
        old = self._paid_order(self.male, self.game, 10000)
        Order.objects.filter(id=old.id).update(created_at='2020-01-01T00:00:00Z')
        self._paid_order(self.female, self.game, 8000)
        self.client.force_authenticate(self.admin)
        kpi = self.client.get(f'{self.URL}?start_date=2025-01-01').data['data']['kpi']
        self.assertEqual(kpi['total_amount'], 8000)

    def test_rank_top_by_metrics(self):
        self._paid_order(self.male, self.game, 10000, provider_income=8000)
        self._paid_order(self.female, self.game, 8000, provider_income=6000)
        self._paid_order(self.female, self.gift, 3000, provider_income=2000)
        self.client.force_authenticate(self.admin)
        rank = self.client.get(self.URL).data['data']['rank']
        # 接单额榜：男 10000 > 女 11000? 女合计 11000 > 男 10000
        self.assertEqual(rank['amount'][0]['value'], 11000)
        self.assertEqual(rank['amount'][0]['gender'], 'FEMALE')
        # 接单量榜：女 2 单 > 男 1 单
        self.assertEqual(rank['count'][0]['value'], 2)
        # 收入榜：女 8000 > 男 8000? 相等取其一，验证最大值
        self.assertEqual(rank['income'][0]['value'], 8000)

    def test_secondary_kpi(self):
        self.male.escort_profile.deposit_paid = 20000
        self.male.escort_profile.total_penalty = 3000
        self.male.escort_profile.total_reward = 1000
        self.male.escort_profile.save()
        make_wallet(self.male, balance=15000, frozen_amount=5000)
        WithdrawRequest.objects.create(
            user=self.male, amount=7000, status=WithdrawRequest.Status.APPROVED,
        )
        self.client.force_authenticate(self.admin)
        sec = self.client.get(self.URL).data['data']['secondary']
        self.assertEqual(sec['total_deposit'], 20000)
        self.assertEqual(sec['total_penalty'], 3000)
        self.assertEqual(sec['total_reward'], 1000)
        self.assertEqual(sec['total_wallet'], 20000)
        self.assertEqual(sec['frozen_amount'], 5000)
        self.assertEqual(sec['settled_salary'], 7000)

    def test_requires_permission(self):
        user = make_console_user(perms=['dashboard:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.URL).status_code, 403)

    def test_perm_allows_view(self):
        user = make_console_user(perms=['player_dashboard:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.URL).status_code, 200)

    def test_non_console_user_denied(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(self.URL).status_code, 403)


class OrderDispatchTest(APITestCase):
    """客服代派单：扣款建单 / 拆账 / 余额拦截 / 可选指派 / 鉴权。"""

    URL = '/api/admin/orders/dispatch/'

    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user(nickname='老板甲')
        make_wallet(self.boss, balance=100000)
        # 默认抽成率 20%
        self.service = make_service(name='上分', price=10000)
        self.provider = make_provider()

    def test_dispatch_without_provider_creates_pending_paid(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 2,
            'remark': '加急',
        })
        self.assertEqual(res.data['code'], 0)
        # 扣款：10000 * 2 = 20000
        wallet = Wallet.objects.get(user=self.boss)
        self.assertEqual(wallet.balance, 80000)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.amount, 20000)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertIsNone(order.provider_id)
        self.assertIsNotNone(order.auto_cancel_at)
        # 拆账：抽成 20% -> 陪玩实得 80%
        self.assertEqual(order.commission_rate, 20)
        self.assertEqual(order.provider_income, 16000)
        self.assertEqual(order.shop_income, 4000)
        self.assertEqual(order.inviter_commission, 0)
        # 流水：一笔 PAY，金额为负
        tx = Transaction.objects.get(wallet=wallet, tx_type=Transaction.TxType.PAY)
        self.assertEqual(tx.amount, -20000)
        # 状态机日志：CREATE 由客服记录
        self.assertTrue(
            order.status_logs.filter(action=OrderStatusLog.Action.CREATE).exists()
        )

    def test_dispatch_with_provider_assigns_grabbed_and_busy(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
            'provider_id': self.provider.id,
        })
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(order.provider_id, self.provider.id)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.status, EscortProfile.Status.BUSY)
        self.assertTrue(
            order.status_logs.filter(action=OrderStatusLog.Action.ASSIGN).exists()
        )

    def test_dispatch_applies_boss_discount(self):
        boss_type = make_boss_type(name='VIP', discount_rate=90)
        self.boss.boss_type = boss_type
        self.boss.save(update_fields=['boss_type'])
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
        })
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        # 10000 * 90% = 9000
        self.assertEqual(order.amount, 9000)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 91000)

    def test_dispatch_splits_inviter_commission(self):
        inviter = make_user(nickname='推荐人')
        self.boss.inviter = inviter
        self.boss.inviter_commission_rate = 50
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
        })
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        # 抽成 20% of 10000 = 2000；推荐人分佣 50% of 2000 = 1000
        self.assertEqual(order.inviter_id, inviter.id)
        self.assertEqual(order.inviter_commission, 1000)
        self.assertEqual(order.shop_income, 1000)
        self.assertEqual(order.provider_income, 8000)

    def test_dispatch_with_fixed_commission(self):
        inviter = make_user(nickname='推荐人')
        self.boss.inviter = inviter
        self.boss.inviter_commission_rate = 50
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        self.client.force_authenticate(self.admin)

        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
            'providers': [{
                'provider_id': self.provider.id,
                'commission_type': OrderProvider.CommissionType.FIXED,
                'commission_fixed': 1500,
            }],
        }, format='json')

        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        provider_row = order.providers.get()
        self.assertEqual(order.amount, 10000)
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(order.provider_id, self.provider.id)
        self.assertEqual(order.commission_rate, 0)
        self.assertEqual(order.provider_income, 8500)
        self.assertEqual(order.inviter_commission, 750)
        self.assertEqual(order.shop_income, 750)
        self.assertEqual(provider_row.commission_type, OrderProvider.CommissionType.FIXED)
        self.assertEqual(provider_row.commission_fixed, 1500)
        self.assertEqual(provider_row.commission_rate, 0)
        self.assertEqual(provider_row.provider_income, 8500)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)

    def test_double_dispatch_preserves_funds(self):
        second = make_provider()
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'escort_mode': Order.EscortMode.DOUBLE,
            'providers': [
                {'provider_id': self.provider.id, 'commission_rate': 20},
                {'provider_id': second.id, 'commission_rate': 30},
            ],
        }, format='json')

        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        rows = list(order.providers.order_by('id'))
        self.assertEqual([row.settlement_base for row in rows], [5000, 5000])
        self.assertEqual([row.provider_income for row in rows], [4000, 3500])
        self.assertEqual(order.provider_income, 7500)
        self.assertEqual(order.inviter_commission, 0)
        self.assertEqual(order.shop_income, 2500)
        self.assertEqual(
            sum(row.provider_income for row in rows)
            + order.inviter_commission + order.shop_income,
            order.amount,
        )

    def test_dispatch_rejected_when_balance_insufficient(self):
        make_wallet(self.boss, balance=1000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
        })
        self.assertEqual(res.data['code'], 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 1000)

    def test_dispatch_rejects_unknown_boss(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': 999999,
            'service_id': self.service.id,
        })
        self.assertEqual(res.data['code'], 404)

    def test_dispatch_rejects_provider_as_boss(self):
        # provider 用户不是 CUSTOMER 角色，应当作老板不存在处理
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.provider.id,
            'service_id': self.service.id,
        })
        self.assertEqual(res.data['code'], 404)

    def test_dispatch_rejects_invalid_rounds(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 0,
        })
        self.assertEqual(res.data['code'], 400)

    def test_dispatch_requires_permission(self):
        user = make_console_user(perms=['order:view'])
        self.client.force_authenticate(user)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
        })
        self.assertEqual(res.status_code, 403)

    def test_dispatch_perm_allows(self):
        user = make_console_user(perms=['order:view', 'order:dispatch'])
        self.client.force_authenticate(user)
        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
        })
        self.assertEqual(res.data['code'], 0)


class AuditionLinkAdminTest(APITestCase):
    """试音链接 CRUD / token 自动生成 / URL 构建 / operator 落库 / 鉴权。"""

    def setUp(self):
        self.admin = make_superuser()

    def test_create_generates_unique_tokens_and_operator(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/audition-links/', {
            'title': '春季招募',
            'remark': '王者',
        })
        self.assertEqual(res.data['code'], 0)
        link = AuditionLink.objects.get(id=res.data['data']['id'])
        self.assertTrue(link.boss_token)
        self.assertTrue(link.provider_token)
        self.assertNotEqual(link.boss_token, link.provider_token)
        self.assertEqual(link.operator_id, self.admin.id)

    def test_create_returns_boss_and_provider_urls(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/audition-links/', {'title': '试音'})
        data = res.data['data']
        self.assertIn(data['boss_token'], data['boss_url'])
        self.assertIn('role=boss', data['boss_url'])
        self.assertIn(data['provider_token'], data['provider_url'])
        self.assertIn('role=provider', data['provider_url'])

    def test_list_and_keyword_filter(self):
        AuditionLink.objects.create(title='王者招募', remark='高分')
        AuditionLink.objects.create(title='和平招募', remark='手游')
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/audition-links/?keyword=王者')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['title'], '王者招募')

    def test_list_is_active_filter(self):
        AuditionLink.objects.create(title='启用', is_active=True)
        AuditionLink.objects.create(title='停用', is_active=False)
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/audition-links/?is_active=0')
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['title'], '停用')

    def test_update_does_not_regenerate_token(self):
        link = AuditionLink.objects.create(title='旧标题')
        old_token = link.boss_token
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'/api/admin/audition-links/{link.id}/', {
            'title': '新标题',
            'is_active': False,
        })
        self.assertEqual(res.data['code'], 0)
        link.refresh_from_db()
        self.assertEqual(link.title, '新标题')
        self.assertFalse(link.is_active)
        self.assertEqual(link.boss_token, old_token)

    def test_destroy(self):
        link = AuditionLink.objects.create(title='待删')
        self.client.force_authenticate(self.admin)
        res = self.client.delete(f'/api/admin/audition-links/{link.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(AuditionLink.objects.filter(id=link.id).exists())

    def test_view_perm_allows_list_denies_create(self):
        user = make_console_user(perms=['audition:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/admin/audition-links/').status_code, 200)
        res = self.client.post('/api/admin/audition-links/', {'title': 'X'})
        self.assertEqual(res.status_code, 403)

    def test_edit_perm_allows_create(self):
        user = make_console_user(perms=['audition:view', 'audition:edit'])
        self.client.force_authenticate(user)
        res = self.client.post('/api/admin/audition-links/', {'title': 'Y'})
        self.assertEqual(res.data['code'], 0)

    def test_delete_perm_required_for_destroy(self):
        link = AuditionLink.objects.create(title='待删')
        user = make_console_user(perms=['audition:view', 'audition:edit'])
        self.client.force_authenticate(user)
        res = self.client.delete(f'/api/admin/audition-links/{link.id}/')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(AuditionLink.objects.filter(id=link.id).exists())

    def test_non_console_user_denied(self):
        customer = make_user()
        self.client.force_authenticate(customer)
        self.assertEqual(self.client.get('/api/admin/audition-links/').status_code, 403)


class CustomerServiceAdminTest(APITestCase):
    """客服信息管理：建客服落 phone/remark / 今日派单额聚合 / 搜索 / 编辑 / 超管保护 / 鉴权。"""

    URL = '/api/admin/admins/'

    def setUp(self):
        self.admin = make_superuser()
        self.role = AdminRole.objects.create(name='派单客服', code='cs', permissions=['order:view'])

    def _row_by_user(self, rows, user_id):
        for row in rows:
            if row['user'] == user_id:
                return row
        return None

    def test_create_persists_phone_and_remark(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'username': 'cs001',
            'password': 'pass1234',
            'nickname': '小客服',
            'phone': '13800001111',
            'remark': '夜班',
            'role': self.role.id,
        })
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['phone'], '13800001111')
        self.assertEqual(data['remark'], '夜班')
        self.assertEqual(data['today_dispatch_amount'], 0)
        membership = AdminMembership.objects.get(id=data['id'])
        self.assertEqual(membership.remark, '夜班')
        self.assertEqual(membership.user.phone, '13800001111')
        self.assertEqual(membership.user.nickname, '小客服')

    def test_today_dispatch_amount_aggregates_create_logs(self):
        cs = make_console_user(perms=['order:view'])
        customer = make_user()
        service = make_service(price=10000)
        order1 = make_order(customer, service=service, amount=20000)
        order2 = make_order(customer, service=service, amount=15000)
        OrderStatusLog.objects.create(order=order1, action=OrderStatusLog.Action.CREATE, operator=cs)
        OrderStatusLog.objects.create(order=order2, action=OrderStatusLog.Action.CREATE, operator=cs)
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL)
        row = self._row_by_user(res.data['data']['list'], cs.id)
        self.assertIsNotNone(row)
        self.assertEqual(row['today_dispatch_amount'], 35000)

    def test_today_dispatch_amount_excludes_other_days(self):
        cs = make_console_user(perms=['order:view'])
        customer = make_user()
        order = make_order(customer, amount=12000)
        log = OrderStatusLog.objects.create(
            order=order, action=OrderStatusLog.Action.CREATE, operator=cs
        )
        # 把日志推到昨天，应不计入今日派单额
        OrderStatusLog.objects.filter(id=log.id).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL)
        row = self._row_by_user(res.data['data']['list'], cs.id)
        self.assertEqual(row['today_dispatch_amount'], 0)

    def test_today_dispatch_amount_excludes_non_create_action(self):
        cs = make_console_user(perms=['order:view'])
        customer = make_user()
        order = make_order(customer, amount=9000)
        OrderStatusLog.objects.create(
            order=order, action=OrderStatusLog.Action.ASSIGN, operator=cs
        )
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL)
        row = self._row_by_user(res.data['data']['list'], cs.id)
        self.assertEqual(row['today_dispatch_amount'], 0)

    def test_keyword_filter_by_phone(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.URL, {
            'username': 'csA', 'password': 'pass1234', 'nickname': '甲',
            'phone': '13900002222', 'role': self.role.id,
        })
        self.client.post(self.URL, {
            'username': 'csB', 'password': 'pass1234', 'nickname': '乙',
            'phone': '13700003333', 'role': self.role.id,
        })
        res = self.client.get(self.URL, {'keyword': '13900002222'})
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['phone'], '13900002222')

    def test_update_changes_nickname_phone_remark(self):
        cs = make_console_user(perms=['order:view'])
        membership = AdminMembership.objects.get(user=cs)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'{self.URL}{membership.id}/', {
            'nickname': '新昵称',
            'phone': '13611112222',
            'remark': '调整为白班',
        })
        self.assertEqual(res.data['code'], 0)
        membership.refresh_from_db()
        cs.refresh_from_db()
        self.assertEqual(cs.nickname, '新昵称')
        self.assertEqual(cs.phone, '13611112222')
        self.assertEqual(membership.remark, '调整为白班')

    def test_update_superuser_role_and_status_protected(self):
        su = make_superuser()
        other_role = AdminRole.objects.create(name='受限', code='limited', permissions=[])
        membership = AdminMembership.objects.create(user=su, role=self.role)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'{self.URL}{membership.id}/', {
            'role': other_role.id,
            'is_active': False,
        })
        self.assertEqual(res.data['code'], 0)
        membership.refresh_from_db()
        # 超管角色与状态不可被改动
        self.assertEqual(membership.role_id, self.role.id)
        self.assertTrue(membership.is_active)

    def test_destroy_superuser_forbidden(self):
        su = make_superuser()
        membership = AdminMembership.objects.create(user=su, role=self.role)
        self.client.force_authenticate(self.admin)
        res = self.client.delete(f'{self.URL}{membership.id}/')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(AdminMembership.objects.filter(id=membership.id).exists())

    def test_view_perm_allows_list_denies_create(self):
        user = make_console_user(perms=['admin:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.URL).status_code, 200)
        res = self.client.post(self.URL, {
            'username': 'x', 'password': 'pass1234', 'role': self.role.id,
        })
        self.assertEqual(res.status_code, 403)

    def test_edit_perm_allows_create(self):
        user = make_console_user(perms=['admin:view', 'admin:edit'])
        self.client.force_authenticate(user)
        res = self.client.post(self.URL, {
            'username': 'csNew', 'password': 'pass1234', 'nickname': '新人',
            'phone': '13500005555', 'role': self.role.id,
        })
        self.assertEqual(res.data['code'], 0)

    def test_non_console_user_denied(self):
        customer = make_user()
        self.client.force_authenticate(customer)
        self.assertEqual(self.client.get(self.URL).status_code, 403)


class EscortLevelAdminTest(APITestCase):
    """陪玩等级 CRUD + 鉴权。"""

    URL = '/api/admin/escort-levels/'

    def setUp(self):
        self.admin = make_superuser()

    def test_create_level(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {'name': '金牌', 'commission_rate': 15})
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['commission_rate'], 15)

    def test_list_levels(self):
        make_escort_level(name='铜牌', commission_rate=30)
        make_escort_level(name='银牌', commission_rate=25)
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['data']['total'], 2)

    def test_list_is_active_filter(self):
        make_escort_level(name='启用', commission_rate=20, is_active=True)
        make_escort_level(name='停用', commission_rate=20, is_active=False)
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL, {'is_active': 'false'})
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['name'], '停用')

    def test_update_level(self):
        level = make_escort_level(name='待改', commission_rate=20)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'{self.URL}{level.id}/', {'commission_rate': 10})
        self.assertEqual(res.data['code'], 0)
        level.refresh_from_db()
        self.assertEqual(level.commission_rate, 10)

    def test_destroy_level(self):
        level = make_escort_level(name='待删', commission_rate=20)
        self.client.force_authenticate(self.admin)
        res = self.client.delete(f'{self.URL}{level.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(EscortLevel.objects.filter(id=level.id).exists())

    def test_view_perm_allows_list_denies_create(self):
        user = make_console_user(perms=['escort_level:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.URL).status_code, 200)
        res = self.client.post(self.URL, {'name': 'X', 'commission_rate': 20})
        self.assertEqual(res.status_code, 403)

    def test_edit_perm_allows_create(self):
        user = make_console_user(perms=['escort_level:view', 'escort_level:edit'])
        self.client.force_authenticate(user)
        res = self.client.post(self.URL, {'name': 'Y', 'commission_rate': 20})
        self.assertEqual(res.data['code'], 0)

    def test_delete_perm_required_for_destroy(self):
        level = make_escort_level(name='保留', commission_rate=20)
        user = make_console_user(perms=['escort_level:view', 'escort_level:edit'])
        self.client.force_authenticate(user)
        res = self.client.delete(f'{self.URL}{level.id}/')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(EscortLevel.objects.filter(id=level.id).exists())

    def test_non_console_user_denied(self):
        customer = make_user()
        self.client.force_authenticate(customer)
        self.assertEqual(self.client.get(self.URL).status_code, 403)


class PromotionAdminTest(APITestCase):
    """促销活动 CRUD + scope/items + 时间窗校验 + 鉴权。"""

    URL = '/api/admin/promotions/'

    def setUp(self):
        self.admin = make_superuser()

    def _payload(self, **kwargs):
        now = timezone.now()
        data = {
            'title': '限时活动',
            'scope': Promotion.Scope.ALL,
            'discount_rate': 90,
            'start_at': (now - timedelta(hours=1)).isoformat(),
            'end_at': (now + timedelta(days=1)).isoformat(),
            'priority': 0,
        }
        data.update(kwargs)
        return data

    def test_create_all_scope(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, self._payload())
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['discount_rate'], 90)
        self.assertEqual(res.data['data']['scope'], Promotion.Scope.ALL)

    def test_create_items_scope_persists_m2m(self):
        s1 = make_service(name='上分', price=10000)
        s2 = make_service(name='娱乐', price=8000)
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, self._payload(
            scope=Promotion.Scope.ITEMS, items=[s1.id, s2.id], discount_rate=None,
            commission_rate=10,
        ), format='json')
        self.assertEqual(res.data['code'], 0)
        promo = Promotion.objects.get(id=res.data['data']['id'])
        self.assertEqual(set(promo.items.values_list('id', flat=True)), {s1.id, s2.id})
        self.assertEqual(promo.commission_rate, 10)
        self.assertIsNone(promo.discount_rate)

    def test_create_rejects_end_before_start(self):
        now = timezone.now()
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, self._payload(
            start_at=(now + timedelta(days=2)).isoformat(),
            end_at=(now + timedelta(days=1)).isoformat(),
        ))
        self.assertEqual(res.status_code, 400)

    def test_list_keyword_filter(self):
        make_promotion(title='双十一大促')
        make_promotion(title='周年庆')
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL, {'keyword': '双十一'})
        self.assertEqual(res.data['data']['total'], 1)

    def test_list_is_active_filter(self):
        make_promotion(title='进行中', is_active=True)
        make_promotion(title='已停用', is_active=False)
        self.client.force_authenticate(self.admin)
        res = self.client.get(self.URL, {'is_active': 'true'})
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['title'], '进行中')

    def test_update_promotion(self):
        promo = make_promotion(title='待改', discount_rate=95)
        self.client.force_authenticate(self.admin)
        res = self.client.patch(f'{self.URL}{promo.id}/', {'discount_rate': 80})
        self.assertEqual(res.data['code'], 0)
        promo.refresh_from_db()
        self.assertEqual(promo.discount_rate, 80)

    def test_destroy_promotion(self):
        promo = make_promotion(title='待删')
        self.client.force_authenticate(self.admin)
        res = self.client.delete(f'{self.URL}{promo.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Promotion.objects.filter(id=promo.id).exists())

    def test_view_perm_allows_list_denies_create(self):
        user = make_console_user(perms=['promotion:view'])
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.URL).status_code, 200)
        res = self.client.post(self.URL, self._payload())
        self.assertEqual(res.status_code, 403)

    def test_edit_perm_allows_create(self):
        user = make_console_user(perms=['promotion:view', 'promotion:edit'])
        self.client.force_authenticate(user)
        res = self.client.post(self.URL, self._payload())
        self.assertEqual(res.data['code'], 0)

    def test_delete_perm_required_for_destroy(self):
        promo = make_promotion(title='保留')
        user = make_console_user(perms=['promotion:view', 'promotion:edit'])
        self.client.force_authenticate(user)
        res = self.client.delete(f'{self.URL}{promo.id}/')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Promotion.objects.filter(id=promo.id).exists())

    def test_non_console_user_denied(self):
        customer = make_user()
        self.client.force_authenticate(customer)
        self.assertEqual(self.client.get(self.URL).status_code, 403)


class EscortLevelAssignmentTest(APITestCase):
    """陪玩管理接口可写 level，并回读 level_name。"""

    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider()
        self.profile = self.provider.escort_profile
        self.level = make_escort_level(name='钻石', commission_rate=12)

    def test_update_assigns_level(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            f'/api/admin/escorts/{self.profile.id}/', {'level': self.level.id}
        )
        self.assertEqual(res.data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.level_id, self.level.id)

    def test_serializer_exposes_level_name(self):
        self.profile.level = self.level
        self.profile.save(update_fields=['level'])
        self.client.force_authenticate(self.admin)
        res = self.client.get(f'/api/admin/escorts/{self.profile.id}/')
        self.assertEqual(res.data['data']['level_name'], '钻石')


class CreateEscortAdminTest(APITestCase):
    """客服后台开户：POST /api/admin/escorts/ 建 PROVIDER 账号 + 陪玩档案。"""

    URL = '/api/admin/escorts/'

    def setUp(self):
        self.admin = make_superuser()

    def test_create_provider_account(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'username': 'escort_new',
            'password': 'pw12345678',
            'display_name': '新晋大神',
            'gender': EscortProfile.Gender.MALE,
            'city': '广州',
        })
        self.assertEqual(res.data['code'], 0)
        from orders.tests.factories import User
        user = User.objects.get(username='escort_new')
        self.assertEqual(user.role, User.Role.PROVIDER)
        self.assertTrue(user.check_password('pw12345678'))
        profile = EscortProfile.objects.get(user=user)
        self.assertEqual(profile.display_name, '新晋大神')
        self.assertEqual(profile.gender, EscortProfile.Gender.MALE)
        # 钱包由 post_save 信号自动创建
        self.assertTrue(Wallet.objects.filter(user=user).exists())

    def test_created_provider_can_login(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.URL, {
            'username': 'escort_login',
            'password': 'pw12345678',
            'display_name': '可登录',
        })
        self.client.force_authenticate(None)
        from django.test import override_settings
        with override_settings(WECHAT_MOCK_LOGIN=False):
            res = self.client.post('/api/users/account-login/', {
                'username': 'escort_login',
                'password': 'pw12345678',
            }, format='json')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['userInfo']['role'], 'provider')

    def test_duplicate_username_rejected(self):
        make_user(role='PROVIDER', username='dup_escort')
        self.client.force_authenticate(self.admin)
        res = self.client.post(self.URL, {
            'username': 'dup_escort',
            'password': 'pw12345678',
            'display_name': '重复',
        })
        self.assertEqual(res.status_code, 400)

    def test_requires_escort_edit_perm(self):
        viewer = make_console_user(perms=['escort:view'])
        self.client.force_authenticate(viewer)
        res = self.client.post(self.URL, {
            'username': 'escort_denied',
            'password': 'pw12345678',
            'display_name': '无权',
        })
        self.assertEqual(res.status_code, 403)
