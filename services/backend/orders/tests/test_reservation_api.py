"""预约接口 R1~R7 的全链路验收（ORD-1/2）。

覆盖：
* R1 发起预约（角色、冲突、时间规则、不能约自己）
* R2 列表（按身份/状态筛选、越权不可见）
* R3 详情（可见性收敛成 404）
* R4 取消 / R5 确认 / R6 拒绝（角色 + 状态机白名单）
* R7 转订单（扣款、分账、派单、幂等、状态回填）

金额一律内部账务单位整数（10 = 1 兴安币）。
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from orders.models import Order, Reservation, ReservationStatusLog
from orders.tests.factories import (
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from users.models import EscortProfile
from wallet.models import Transaction, Wallet

LIST_URL = '/api/orders/reservations/'


def slot(days_ahead=1, hour=10, minute=0):
    """对齐到 15 分钟刻度且落在未来的时间点。"""
    return (timezone.now() + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )


def iso(dt):
    return dt.isoformat()


class ReservationApiTestBase(APITestCase):
    """公共夹具：一个老板、一个陪玩、一个服务项，钱包按需注资。"""

    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, self.boss_account)
        make_wallet(self.boss, balance=100000)
        Wallet.objects.filter(user=self.boss).update(account=self.boss_account)

        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        link_legacy_relations(self.provider, self.provider_account)
        EscortProfile.objects.filter(user=self.provider).update(
            account=self.provider_account,
        )
        make_wallet(self.provider, balance=0)
        Wallet.objects.filter(user=self.provider).update(
            account=self.provider_account,
        )

        self.staff = make_user(role='OPERATOR')
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        link_legacy_relations(self.staff, self.staff_account)

        self.service = make_service(price=1000)
        self.client.force_authenticate(self.boss)

    def create_payload(self, **overrides):
        payload = {
            'provider_account_id': self.provider_account.id,
            'service_id': self.service.id,
            'start_time': iso(slot(hour=10)),
            'end_time': iso(slot(hour=12)),
            'game_rounds': 2,
            'remark': '带我上分',
        }
        payload.update(overrides)
        return payload

    def create_reservation_via_api(self, **overrides):
        res = self.client.post(LIST_URL, self.create_payload(**overrides))
        self.assertEqual(res.data['code'], 0, res.data.get('msg'))
        return Reservation.objects.get(id=res.data['data']['id'])


class ReservationCreateApiTest(ReservationApiTestBase):
    """R1 发起预约。"""

    def test_boss_creates_reservation(self):
        res = self.client.post(LIST_URL, self.create_payload())
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['status'], Reservation.Status.PENDING)
        self.assertEqual(data['duration_minutes'], 120)
        self.assertEqual(data['estimated_amount'], 2000)  # 1000 * 2 局
        self.assertTrue(data['reservation_no'].startswith('RSV'))
        self.assertEqual(data['provider']['account_id'], self.provider_account.id)

        reservation = Reservation.objects.get(id=data['id'])
        self.assertEqual(reservation.customer_account_id, self.boss_account.id)
        self.assertEqual(reservation.provider_id, self.provider.id)
        # 创建即写审计
        log = ReservationStatusLog.objects.get(reservation=reservation)
        self.assertEqual(log.action, ReservationStatusLog.Action.CREATE)

    def test_provider_cannot_create(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(LIST_URL, self.create_payload())
        self.assertEqual(res.data['code'], 403)
        self.assertIn('仅玩家或客服', res.data['msg'])

    def test_staff_can_create_on_behalf(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(LIST_URL, self.create_payload())
        self.assertEqual(res.data['code'], 0)

    def test_rejects_unknown_provider(self):
        res = self.client.post(
            LIST_URL, self.create_payload(provider_account_id=999999),
        )
        self.assertEqual(res.data['code'], 400)
        self.assertIn('大神不存在', res.data['msg'])

    def test_rejects_unknown_service(self):
        res = self.client.post(LIST_URL, self.create_payload(service_id=999999))
        self.assertEqual(res.data['code'], 400)
        self.assertIn('服务不存在', res.data['msg'])

    def test_rejects_self_reservation(self):
        self.client.force_authenticate(self.provider)
        # 陪玩连发起权限都没有，这里换成客服拿陪玩账户预约自己的场景
        self.client.force_authenticate(self.staff)
        res = self.client.post(
            LIST_URL, self.create_payload(provider_account_id=self.staff_account.id),
        )
        # 客服账户不是 PROVIDER，先在序列化器层被拦
        self.assertEqual(res.data['code'], 400)

    # ---- 时间规则 5 条在接口层同样生效 ----
    def test_rejects_unaligned_time(self):
        res = self.client.post(
            LIST_URL, self.create_payload(start_time=iso(slot(hour=10, minute=7))),
        )
        self.assertEqual(res.data['code'], 400)
        self.assertIn('15 分钟刻度', res.data['msg'])

    def test_rejects_past_time(self):
        past_start = (timezone.now() - timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0,
        )
        res = self.client.post(LIST_URL, self.create_payload(
            start_time=iso(past_start),
            end_time=iso(past_start + timedelta(hours=2)),
        ))
        self.assertEqual(res.data['code'], 400)
        self.assertIn('晚于当前时间', res.data['msg'])

    def test_rejects_reversed_window(self):
        res = self.client.post(LIST_URL, self.create_payload(
            start_time=iso(slot(hour=12)), end_time=iso(slot(hour=10)),
        ))
        self.assertEqual(res.data['code'], 400)
        self.assertIn('晚于开始时间', res.data['msg'])

    def test_rejects_too_short_window(self):
        res = self.client.post(LIST_URL, self.create_payload(
            end_time=iso(slot(hour=10, minute=15)),
        ))
        self.assertEqual(res.data['code'], 400)
        self.assertIn('30 分钟', res.data['msg'])

    def test_rejects_beyond_advance_limit(self):
        res = self.client.post(LIST_URL, self.create_payload(
            start_time=iso(slot(days_ahead=40, hour=10)),
            end_time=iso(slot(days_ahead=40, hour=12)),
        ))
        self.assertEqual(res.data['code'], 400)
        self.assertIn('30 天', res.data['msg'])

    def test_conflict_returns_409(self):
        self.create_reservation_via_api()
        res = self.client.post(LIST_URL, self.create_payload(
            start_time=iso(slot(hour=11)), end_time=iso(slot(hour=13)),
        ))
        self.assertEqual(res.data['code'], 409)
        self.assertIn('已有预约', res.data['msg'])
        self.assertEqual(Reservation.objects.count(), 1)


class ReservationListDetailApiTest(ReservationApiTestBase):
    """R2 列表 / R3 详情。"""

    def setUp(self):
        super().setUp()
        self.reservation = self.create_reservation_via_api()

    def test_boss_sees_own_reservation(self):
        res = self.client.get(LIST_URL)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(
            res.data['data']['items'][0]['id'], self.reservation.id,
        )

    def test_provider_sees_incoming_reservation(self):
        self.client.force_authenticate(self.provider)
        res = self.client.get(LIST_URL)
        self.assertEqual(res.data['data']['total'], 1)
        actions = res.data['data']['items'][0]['allowed_actions']
        self.assertIn('confirm', actions)
        self.assertIn('reject', actions)

    def test_outsider_sees_nothing(self):
        outsider = make_user()
        self.client.force_authenticate(outsider)
        res = self.client.get(LIST_URL)
        self.assertEqual(res.data['data']['total'], 0)

    def test_status_filter(self):
        res = self.client.get(LIST_URL, {'status': 'pending'})
        self.assertEqual(res.data['data']['total'], 1)
        res = self.client.get(LIST_URL, {'status': 'confirmed'})
        self.assertEqual(res.data['data']['total'], 0)

    def test_unknown_status_filter_returns_400(self):
        res = self.client.get(LIST_URL, {'status': 'whatever'})
        self.assertEqual(res.data['code'], 400)
        self.assertIn('不支持的预约状态', res.data['msg'])

    def test_detail_ok(self):
        res = self.client.get(f'{LIST_URL}{self.reservation.id}/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['reservation_no'],
                         self.reservation.reservation_no)

    def test_detail_hidden_from_outsider(self):
        self.client.force_authenticate(make_user())
        res = self.client.get(f'{LIST_URL}{self.reservation.id}/')
        self.assertEqual(res.data['code'], 404)

    def test_detail_missing_returns_404(self):
        res = self.client.get(f'{LIST_URL}999999/')
        self.assertEqual(res.data['code'], 404)

    def test_pagination_caps_page_size(self):
        res = self.client.get(LIST_URL, {'page': 1, 'page_size': 500})
        self.assertEqual(res.data['data']['page_size'], 50)


class ReservationTransitionApiTest(ReservationApiTestBase):
    """R4 取消 / R5 确认 / R6 拒绝。"""

    def setUp(self):
        super().setUp()
        self.reservation = self.create_reservation_via_api()
        self.confirm_url = f'{LIST_URL}{self.reservation.id}/confirm/'
        self.reject_url = f'{LIST_URL}{self.reservation.id}/reject/'
        self.cancel_url = f'{LIST_URL}{self.reservation.id}/cancel/'

    def test_provider_confirms(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.confirm_url, {})
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['status'], Reservation.Status.CONFIRMED)
        self.reservation.refresh_from_db()
        self.assertIsNotNone(self.reservation.confirmed_at)
        self.assertTrue(
            ReservationStatusLog.objects.filter(
                reservation=self.reservation,
                action=ReservationStatusLog.Action.CONFIRM,
            ).exists()
        )

    def test_boss_cannot_confirm(self):
        res = self.client.post(self.confirm_url, {})
        self.assertEqual(res.data['code'], 403)
        self.assertIn('仅被预约的大神', res.data['msg'])

    def test_staff_can_confirm(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(self.confirm_url, {})
        self.assertEqual(res.data['code'], 0)

    def test_double_confirm_returns_409(self):
        self.client.force_authenticate(self.provider)
        self.client.post(self.confirm_url, {})
        res = self.client.post(self.confirm_url, {})
        self.assertEqual(res.data['code'], 409)
        self.assertIn('不允许该操作', res.data['msg'])

    def test_provider_rejects_with_reason(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.reject_url, {'reason': '当天有事'})
        self.assertEqual(res.data['code'], 0)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.REJECTED)
        self.assertEqual(self.reservation.reject_reason, '当天有事')
        self.assertIsNotNone(self.reservation.rejected_at)

    def test_rejected_slot_is_reusable(self):
        self.client.force_authenticate(self.provider)
        self.client.post(self.reject_url, {'reason': '有事'})
        self.client.force_authenticate(self.boss)
        res = self.client.post(LIST_URL, self.create_payload())
        self.assertEqual(res.data['code'], 0)

    def test_boss_cancels_pending(self):
        res = self.client.post(self.cancel_url, {'reason': '临时改期'})
        self.assertEqual(res.data['code'], 0)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(self.reservation.cancel_reason, '临时改期')

    def test_provider_cannot_cancel_pending(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.cancel_url, {})
        self.assertEqual(res.data['code'], 403)
        self.assertIn('无权取消', res.data['msg'])

    def test_provider_can_cancel_confirmed(self):
        self.client.force_authenticate(self.provider)
        self.client.post(self.confirm_url, {})
        res = self.client.post(self.cancel_url, {'reason': '临时有事'})
        self.assertEqual(res.data['code'], 0)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)

    def test_cancel_after_cancel_returns_409(self):
        self.client.post(self.cancel_url, {})
        res = self.client.post(self.cancel_url, {})
        self.assertEqual(res.data['code'], 409)

    def test_outsider_gets_404_not_403(self):
        self.client.force_authenticate(make_user())
        res = self.client.post(self.cancel_url, {})
        self.assertEqual(res.data['code'], 404)

    def test_confirm_missing_reservation_returns_404(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(f'{LIST_URL}999999/confirm/', {})
        self.assertEqual(res.data['code'], 404)


class ReservationConvertApiTest(ReservationApiTestBase):
    """R7 预约转订单：唯一动钱的一步。"""

    def setUp(self):
        super().setUp()
        self.reservation = self.create_reservation_via_api()
        self.convert_url = f'{LIST_URL}{self.reservation.id}/convert/'
        # 先由陪玩确认，转单前置条件是 CONFIRMED
        self.client.force_authenticate(self.provider)
        self.client.post(f'{LIST_URL}{self.reservation.id}/confirm/', {})
        self.client.force_authenticate(self.boss)

    def test_convert_creates_paid_order(self):
        res = self.client.post(self.convert_url, {'client_request_id': 'cvt-1'})
        self.assertEqual(res.data['code'], 0, res.data.get('msg'))

        order = Order.objects.get(id=res.data['data']['order']['id'])
        self.assertEqual(order.amount, 2000)  # 1000 * 2 局
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        # 预约已钉死陪玩：直接进入已接单，不入抢单池
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(order.provider_id, self.provider.id)
        self.assertEqual(order.provider_account_id, self.provider_account.id)
        self.assertIsNone(order.auto_cancel_at)

        # 扣款与流水
        wallet = Wallet.objects.get(user=self.boss)
        self.assertEqual(wallet.balance, 98000)
        tx = Transaction.objects.get(order=order, tx_type=Transaction.TxType.PAY)
        self.assertEqual(tx.amount, -2000)
        self.assertEqual(tx.balance_before, 100000)
        self.assertEqual(tx.balance_after, 98000)

        # 分账守恒
        self.assertEqual(
            order.provider_income + order.inviter_commission + order.shop_income,
            order.amount,
        )

        # 预约进入终态并回填订单
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONVERTED)
        self.assertEqual(self.reservation.order_id, order.id)
        self.assertIsNotNone(self.reservation.converted_at)
        self.assertTrue(
            ReservationStatusLog.objects.filter(
                reservation=self.reservation,
                action=ReservationStatusLog.Action.CONVERT,
            ).exists()
        )

    def test_convert_requires_confirmed(self):
        fresh = self.create_reservation_via_api(
            start_time=iso(slot(hour=14)), end_time=iso(slot(hour=16)),
        )
        res = self.client.post(f'{LIST_URL}{fresh.id}/convert/', {})
        self.assertEqual(res.data['code'], 409)
        self.assertIn('仅已确认', res.data['msg'])
        self.assertFalse(Order.objects.exists())

    def test_convert_rejects_insufficient_balance(self):
        Wallet.objects.filter(user=self.boss).update(balance=100)
        res = self.client.post(self.convert_url, {})
        self.assertEqual(res.data['code'], 400)
        self.assertIn('兴安币不足', res.data['msg'])
        self.assertFalse(Order.objects.exists())
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)

    def test_convert_is_idempotent_on_replay(self):
        first = self.client.post(self.convert_url, {'client_request_id': 'cvt-9'})
        self.assertEqual(first.data['code'], 0)
        second = self.client.post(self.convert_url, {'client_request_id': 'cvt-9'})
        self.assertEqual(second.data['code'], 409)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(
            Transaction.objects.filter(tx_type=Transaction.TxType.PAY).count(), 1,
        )

    def test_second_convert_without_key_blocked_by_state(self):
        self.client.post(self.convert_url, {})
        res = self.client.post(self.convert_url, {})
        # 预约已是 CONVERTED，状态机这道闸门同样兜得住
        self.assertEqual(res.data['code'], 409)
        self.assertEqual(Order.objects.count(), 1)

    def test_provider_cannot_convert(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.convert_url, {})
        self.assertEqual(res.data['code'], 403)
        self.assertIn('仅玩家或客服', res.data['msg'])
        self.assertFalse(Order.objects.exists())

    def test_staff_can_convert_on_behalf_and_charges_the_boss(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(self.convert_url, {})
        self.assertEqual(res.data['code'], 0, res.data.get('msg'))
        # 钱必须从老板钱包出，而不是客服钱包
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 98000)
        order = Order.objects.get()
        self.assertEqual(order.customer_id, self.boss.id)
        self.assertEqual(order.customer_account_id, self.boss_account.id)

    def test_other_boss_cannot_convert(self):
        stranger = make_user()
        self.client.force_authenticate(stranger)
        res = self.client.post(self.convert_url, {})
        self.assertEqual(res.data['code'], 404)
        self.assertFalse(Order.objects.exists())

    def test_convert_missing_reservation_returns_404(self):
        res = self.client.post(f'{LIST_URL}999999/convert/', {})
        self.assertEqual(res.data['code'], 404)

    def test_cancelled_reservation_cannot_convert(self):
        self.client.post(f'{LIST_URL}{self.reservation.id}/cancel/', {})
        res = self.client.post(self.convert_url, {})
        self.assertEqual(res.data['code'], 409)
        self.assertFalse(Order.objects.exists())
