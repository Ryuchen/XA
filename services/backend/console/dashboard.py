"""陪玩看板按订单分账快照归属收入，兼容没有成员明细的旧单。"""
from django.db.models import Count, Q, Sum
from rest_framework import serializers

from club_accounts.models import ClubAccount, LegacyAccountMap
from orders.models import OrderProvider
from users.models import EscortProfile


class DashboardRangeSerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        if attrs.get('start_date') and attrs.get('end_date') and attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError('开始日期不能晚于结束日期')
        return attrs


def player_order_metrics(order_qs):
    from orders.refunds import with_net_amount
    from orders.analytics import partial_share_rows
    kpi = dict.fromkeys(('total_amount', 'game_amount', 'gift_amount', 'male_game',
                         'female_game', 'male_gift', 'female_gift'), 0)
    for row in with_net_amount(order_qs).values('service__service_category__is_gift').annotate(amount=Sum('_net_paid_amount')):
        amount = row['amount'] or 0
        kpi['total_amount'] += amount
        kpi['gift_amount' if row['service__service_category__is_gift'] else 'game_amount'] += amount

    # 先按成员和商品类型在数据库聚合，避免读取全量订单到进程内存。
    snapshots = order_qs.exclude(payment_status='PARTIALLY_REFUNDED')
    shares = OrderProvider.objects.filter(order__in=snapshots).filter(
        Q(released_at__isnull=True) | Q(settlement_base__gt=0) | Q(provider_income__gt=0),
    )
    rows = []
    for row in shares.values('provider_account_id', 'provider_id', 'order__service__service_category__is_gift').annotate(
        amount=Sum('settlement_base'), income=Sum('provider_income'), count=Count('order_id', distinct=True),
    ):
        rows.append({**row, 'is_gift': row['order__service__service_category__is_gift']})
    for row in snapshots.filter(providers__isnull=True).values(
        'provider_account_id', 'provider_id', 'service__service_category__is_gift',
    ).annotate(amount=Sum('amount'), income=Sum('provider_income'), count=Count('id')):
        rows.append({**row, 'is_gift': row['service__service_category__is_gift']})
    rows.extend(partial_share_rows(order_qs))

    legacy_ids = {row['provider_id'] for row in rows if row['provider_id']}
    mappings = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=legacy_ids).values_list('legacy_user_id', 'account_id'))
    account_ids = {row['provider_account_id'] or mappings.get(row['provider_id']) for row in rows}
    accounts = ClubAccount.objects.in_bulk(account_ids - {None})
    profiles = list(EscortProfile.objects.filter(Q(account_id__in=account_ids - {None}) | Q(user_id__in=legacy_ids)).select_related('user'))
    by_account = {profile.account_id: profile for profile in profiles if profile.account_id}
    by_legacy = {profile.user_id: profile for profile in profiles}
    providers = {}
    for row in rows:
        account_id = row['provider_account_id'] or mappings.get(row['provider_id'])
        if not account_id and not row['provider_id']:
            continue
        account = accounts.get(account_id)
        profile = by_account.get(account_id) or by_legacy.get(row['provider_id'])
        key = ('account', account_id) if account_id else ('legacy', row['provider_id'])
        gender = profile.gender if profile else EscortProfile.Gender.UNKNOWN
        nickname = (profile.display_name if profile else '') or (account.nickname or account.username if account else '')
        if not nickname and profile:
            nickname = profile.user.nickname or profile.user.username
        item = providers.setdefault(key, {
            'nickname': nickname or '历史陪玩',
            'escort_no': profile.escort_no if profile else (account.account_no if account else ''),
            'gender': gender, 'amount': 0, 'income': 0, 'count': 0,
        })
        for metric in ('amount', 'income', 'count'):
            item[metric] += row[metric] or 0
        if gender in (EscortProfile.Gender.MALE, EscortProfile.Gender.FEMALE):
            kpi[f'{gender.lower()}_{"gift" if row["is_gift"] else "game"}'] += row['amount'] or 0

    rank = {}
    for metric in ('amount', 'income', 'count'):
        ranked = sorted(providers.values(), key=lambda item: (-item[metric], item['escort_no'], item['nickname']))[:10]
        rank[metric] = [{name: item[name] for name in ('nickname', 'escort_no', 'gender')} | {'value': item[metric]} for item in ranked]
    return kpi, rank
