"""Read-only store-local inventory of evidence for historic monthly coupons."""
import json
from collections import Counter
from datetime import date
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from club_accounts.models import ClubAccount
from coupons.models import UserCoupon
from engagement.models import VipTier


def audit_monthly(database, *, month):
    if not isinstance(month, date) or month.day != 1:
        raise ValueError('月份须为当月第一天')
    month_text = month.strftime('%Y-%m')
    accounts = list(ClubAccount.objects.using(database).filter(account_type='BOSS', is_active=True)
        .values('id', 'business_cycle', 'boss_type_id').order_by('id'))
    tiers = {row['boss_type_id']: row for row in VipTier.objects.using(database).filter(
        active=True, boss_type__is_active=True).values('boss_type_id', 'level', 'monthly_coupon_count', 'monthly_discount')}
    evidence, issues = Counter(), Counter()
    rows = UserCoupon.objects.using(database).filter(coupon__grant_key__startswith='vip-month:').values_list(
        'account_id', 'business_cycle', 'coupon__grant_key')
    for account_id, cycle, key in rows.iterator():
        parts = key.split(':') if key else []
        if len(parts) < 6 or parts[0] != 'vip-month' or not all(x.isascii() and x.isdigit() for x in (parts[1], parts[2])):
            issues['malformed_key'] += 1
            continue
        if parts[3] != month_text:
            continue
        if account_id != int(parts[1]) or cycle != int(parts[2]):
            issues['owner_or_cycle_mismatch'] += 1
            continue
        evidence[(account_id, cycle)] += 1
    records = []
    for account in accounts:
        tier = tiers.get(account['boss_type_id'])
        count = evidence[(account['id'], account['business_cycle'])]
        records.append({'account_id': account['id'], 'business_cycle': account['business_cycle'],
            'active_tier_level': tier['level'] if tier else None,
            'current_configured_count': tier['monthly_coupon_count'] if tier else None,
            'current_configured_discount': tier['monthly_discount'] if tier else None,
            'recorded_coupon_count': count,
            'evidence': 'HAS_COUPONS' if count else 'NO_COUPONS_CANNOT_PROVE_ZERO_GRANT'})
    return {'database_alias':database, 'month':month_text, 'account_count':len(records),
        'with_coupons':sum(row['recorded_coupon_count'] > 0 for row in records),
        'without_coupons':sum(row['recorded_coupon_count'] == 0 for row in records),
        'issues':dict(issues), 'records':records}


class Command(BaseCommand):
    help = '只读盘点指定门店月券历史证据；不会发券、创建账户或修改记录'

    def add_arguments(self, parser):
        parser.add_argument('--database', required=True, help='明确指定单个门店业务库别名')
        parser.add_argument('--month', help='北京时间月份 YYYY-MM，默认本月')

    def handle(self, *args, **options):
        database = options['database']
        if database not in connections:
            raise CommandError('未知数据库别名')
        value = options['month'] or timezone.localtime(timezone.now(), ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
        try:
            month = date.fromisoformat(value + '-01')
            if month.strftime('%Y-%m') != value:
                raise ValueError
        except (TypeError, ValueError):
            raise CommandError('月份须为 YYYY-MM') from None
        self.stdout.write(json.dumps(audit_monthly(database, month=month), ensure_ascii=False))
