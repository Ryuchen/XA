"""预约时间冲突检测的边界与并发验收（ORD-1）。

冲突判定式（半开区间 ``[start, end)``）::

    N.start_time < E.end_time AND N.end_time > E.start_time

九个边界位置（E 为已存在的 10:00-12:00 预约）::

    ①  08:00-09:00   完全早于        不冲突
    ②  09:00-10:00   首尾相接(前)    不冲突
    ③  09:00-11:00   左侧重叠        冲突
    ④  10:00-12:00   完全相同        冲突
    ⑤  10:30-11:30   被包含          冲突
    ⑥  09:00-13:00   完全包含        冲突
    ⑦  11:00-13:00   右侧重叠        冲突
    ⑧  12:00-13:00   首尾相接(后)    不冲突
    ⑨  13:00-14:00   完全晚于        不冲突
"""

from datetime import timedelta

from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from orders.models import Reservation
from orders.reservation_services import (
    MAX_ADVANCE_DAYS,
    MAX_DURATION_HOURS,
    MIN_DURATION_MINUTES,
    SLOT_MINUTES,
    ReservationConflictError,
    ReservationWindowError,
    create_reservation,
    find_conflicting_reservations,
    validate_reservation_window,
)
from orders.tests.factories import make_provider, make_service, make_user
from users.models import EscortProfile


def bind(user):
    """给 legacy 用户补 ClubAccount 并回填陪玩档案的 account 维度。"""
    account = get_or_create_account_for_legacy_user(user)
    link_legacy_relations(user, account)
    EscortProfile.objects.filter(user=user).update(account=account)
    return account


def slot(days_ahead=1, hour=10, minute=0):
    """构造一个对齐到刻度、且落在未来的时间点。"""
    base = timezone.now() + timedelta(days=days_ahead)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


class ValidateReservationWindowTest(TestCase):
    """五条时间规则各自的正反面。"""

    def test_accepts_aligned_future_window(self):
        start = slot(hour=10)
        end = slot(hour=12)
        self.assertEqual(validate_reservation_window(start, end), 120)

    def test_rejects_end_before_start(self):
        with self.assertRaises(ReservationWindowError) as ctx:
            validate_reservation_window(slot(hour=12), slot(hour=10))
        self.assertIn('必须晚于开始时间', str(ctx.exception))

    def test_rejects_equal_start_and_end(self):
        moment = slot(hour=10)
        with self.assertRaises(ReservationWindowError):
            validate_reservation_window(moment, moment)

    def test_rejects_unaligned_minute(self):
        start = slot(hour=10, minute=7)
        end = slot(hour=12)
        with self.assertRaises(ReservationWindowError) as ctx:
            validate_reservation_window(start, end)
        self.assertIn(f'{SLOT_MINUTES} 分钟刻度', str(ctx.exception))

    def test_rejects_unaligned_second(self):
        start = slot(hour=10).replace(second=30)
        with self.assertRaises(ReservationWindowError):
            validate_reservation_window(start, slot(hour=12))

    def test_accepts_quarter_hour_alignment(self):
        start = slot(hour=10, minute=45)
        end = slot(hour=11, minute=45)
        self.assertEqual(validate_reservation_window(start, end), 60)

    def test_rejects_past_start(self):
        now = timezone.now().replace(second=0, microsecond=0)
        start = (now - timedelta(days=1)).replace(hour=10, minute=0)
        end = (now - timedelta(days=1)).replace(hour=12, minute=0)
        with self.assertRaises(ReservationWindowError) as ctx:
            validate_reservation_window(start, end)
        self.assertIn('晚于当前时间', str(ctx.exception))

    def test_rejects_too_short(self):
        start = slot(hour=10)
        end = slot(hour=10, minute=15)
        with self.assertRaises(ReservationWindowError) as ctx:
            validate_reservation_window(start, end)
        self.assertIn(f'{MIN_DURATION_MINUTES} 分钟', str(ctx.exception))

    def test_accepts_exact_min_duration(self):
        start = slot(hour=10)
        end = slot(hour=10, minute=30)
        self.assertEqual(
            validate_reservation_window(start, end), MIN_DURATION_MINUTES,
        )

    def test_rejects_too_long(self):
        start = slot(hour=8)
        end = slot(hour=8) + timedelta(hours=MAX_DURATION_HOURS, minutes=15)
        with self.assertRaises(ReservationWindowError) as ctx:
            validate_reservation_window(start, end)
        self.assertIn(f'{MAX_DURATION_HOURS} 小时', str(ctx.exception))

    def test_accepts_exact_max_duration(self):
        start = slot(hour=8)
        end = start + timedelta(hours=MAX_DURATION_HOURS)
        self.assertEqual(
            validate_reservation_window(start, end), MAX_DURATION_HOURS * 60,
        )

    def test_rejects_beyond_advance_limit(self):
        start = slot(days_ahead=MAX_ADVANCE_DAYS + 2, hour=10)
        end = slot(days_ahead=MAX_ADVANCE_DAYS + 2, hour=12)
        with self.assertRaises(ReservationWindowError) as ctx:
            validate_reservation_window(start, end)
        self.assertIn(f'{MAX_ADVANCE_DAYS} 天', str(ctx.exception))

    def test_rejects_missing_times(self):
        with self.assertRaises(ReservationWindowError):
            validate_reservation_window(None, slot(hour=12))


class ReservationConflictBoundaryTest(TestCase):
    """九个边界位置的冲突判定。"""

    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, self.boss_account)
        self.provider = make_provider()
        self.provider_account = bind(self.provider)
        self.provider_account.account_type = ClubAccount.AccountType.PROVIDER
        self.provider_account.save(update_fields=['account_type'])
        self.service = make_service(price=1000)

        self.existing = create_reservation(
            customer_account=self.boss_account,
            customer=self.boss,
            provider_account=self.provider_account,
            service=self.service,
            start_time=slot(hour=10),
            end_time=slot(hour=12),
        )

    def _conflicts(self, start_hour, end_hour, start_minute=0, end_minute=0):
        return find_conflicting_reservations(
            self.provider_account,
            slot(hour=start_hour, minute=start_minute),
            slot(hour=end_hour, minute=end_minute),
        ).exists()

    def test_1_entirely_before(self):
        self.assertFalse(self._conflicts(8, 9))

    def test_2_touching_before(self):
        self.assertFalse(self._conflicts(9, 10))

    def test_3_overlap_left(self):
        self.assertTrue(self._conflicts(9, 11))

    def test_4_identical(self):
        self.assertTrue(self._conflicts(10, 12))

    def test_5_contained(self):
        self.assertTrue(self._conflicts(10, 11, start_minute=30, end_minute=30))

    def test_6_containing(self):
        self.assertTrue(self._conflicts(9, 13))

    def test_7_overlap_right(self):
        self.assertTrue(self._conflicts(11, 13))

    def test_8_touching_after(self):
        self.assertFalse(self._conflicts(12, 13))

    def test_9_entirely_after(self):
        self.assertFalse(self._conflicts(13, 14))

    # ---- 状态维度：非占用态不参与冲突 ----
    def test_cancelled_reservation_frees_the_slot(self):
        self.existing.status = Reservation.Status.CANCELLED
        self.existing.save(update_fields=['status'])
        self.assertFalse(self._conflicts(10, 12))

    def test_rejected_reservation_frees_the_slot(self):
        self.existing.status = Reservation.Status.REJECTED
        self.existing.save(update_fields=['status'])
        self.assertFalse(self._conflicts(10, 12))

    def test_converted_reservation_frees_the_slot(self):
        self.existing.status = Reservation.Status.CONVERTED
        self.existing.save(update_fields=['status'])
        self.assertFalse(self._conflicts(10, 12))

    def test_confirmed_reservation_still_occupies(self):
        self.existing.status = Reservation.Status.CONFIRMED
        self.existing.save(update_fields=['status'])
        self.assertTrue(self._conflicts(10, 12))

    def test_other_provider_is_not_affected(self):
        other = make_provider()
        other_account = bind(other)
        other_account.account_type = ClubAccount.AccountType.PROVIDER
        other_account.save(update_fields=['account_type'])
        self.assertFalse(
            find_conflicting_reservations(
                other_account, slot(hour=10), slot(hour=12),
            ).exists()
        )

    def test_exclude_self_for_reschedule(self):
        self.assertFalse(
            find_conflicting_reservations(
                self.provider_account,
                slot(hour=10),
                slot(hour=12),
                exclude_id=self.existing.id,
            ).exists()
        )

    # ---- create_reservation 落地时同样必须拒绝 ----
    def test_create_raises_on_overlap(self):
        with self.assertRaises(ReservationConflictError) as ctx:
            create_reservation(
                customer_account=self.boss_account,
                customer=self.boss,
                provider_account=self.provider_account,
                service=self.service,
                start_time=slot(hour=11),
                end_time=slot(hour=13),
            )
        self.assertIn('已有预约', str(ctx.exception))
        self.assertEqual(Reservation.objects.count(), 1)

    def test_create_allows_touching_window(self):
        second = create_reservation(
            customer_account=self.boss_account,
            customer=self.boss,
            provider_account=self.provider_account,
            service=self.service,
            start_time=slot(hour=12),
            end_time=slot(hour=14),
        )
        self.assertEqual(second.status, Reservation.Status.PENDING)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(second.duration_minutes, 120)


class ReservationPessimisticLockTest(TransactionTestCase):
    """悲观锁验收：串行重放两次「同时」下单，第二次必须被拒。

    真并发线程在 sqlite 上会退化成库级锁，断言不稳定；这里改为验证
    「档期锁 + 查询」这条路径在同一时间片上不可能落两条占用态预约 ——
    即冲突检测不依赖任何数据库排他约束也能守住不变量。
    """

    reset_sequences = True

    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, self.boss_account)
        self.provider = make_provider()
        self.provider_account = bind(self.provider)
        self.provider_account.account_type = ClubAccount.AccountType.PROVIDER
        self.provider_account.save(update_fields=['account_type'])
        self.service = make_service(price=1000)

    def _create(self, start_hour, end_hour):
        return create_reservation(
            customer_account=self.boss_account,
            customer=self.boss,
            provider_account=self.provider_account,
            service=self.service,
            start_time=slot(hour=start_hour),
            end_time=slot(hour=end_hour),
        )

    def test_second_request_on_same_slot_is_rejected(self):
        self._create(10, 12)
        with self.assertRaises(ReservationConflictError):
            self._create(10, 12)
        self.assertEqual(
            Reservation.objects.filter(
                provider_account=self.provider_account,
                status__in=[
                    Reservation.Status.PENDING, Reservation.Status.CONFIRMED,
                ],
            ).count(),
            1,
        )

    def test_no_active_overlap_invariant_after_burst(self):
        """连续插入多个时间片，任意两条占用态预约都不得重叠。"""
        for hour in (8, 10, 12, 14):
            self._create(hour, hour + 2)
        # 中间插一条必然冲突的，应被拒
        with self.assertRaises(ReservationConflictError):
            self._create(9, 11)

        active = list(
            Reservation.objects.filter(
                provider_account=self.provider_account,
                status__in=[
                    Reservation.Status.PENDING, Reservation.Status.CONFIRMED,
                ],
            ).order_by('start_time')
        )
        self.assertEqual(len(active), 4)
        for earlier, later in zip(active, active[1:]):
            self.assertLessEqual(earlier.end_time, later.start_time)
