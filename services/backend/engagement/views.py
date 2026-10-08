from django.db import transaction
from django.db.models import Q, Sum, Count
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.permissions import IsClubAccountAuthenticated
from orders.models import Order
from users.models import Achievement
from wallet.models import RechargeRecord
from .models import (Broadcast, ClubContent, LotteryCampaign, LotteryDraw, MemberProfile,
    MemberTag, PointLedger, RechargePackage, RewardClaim, RewardItem, VipTier)
from .serializers import (CampaignSerializer, ClaimDetailsSerializer, ClaimSerializer, ContentSerializer,
    MemberEditSerializer, RewardSerializer, TierSerializer)
from .services import draw, locked_profile, redeem, completed_consumption


class IsBoss(IsClubAccountAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.account.account_type == 'BOSS' and request.account.can_view


class IsPointsMember(IsClubAccountAuthenticated):
    def has_permission(self, request, view):
        from .kook_points import allowed_account
        return super().has_permission(request, view) and allowed_account(request.account)


def available_tags(account):
    tags = [{'key': f'tag:{tag.pk}', 'title': tag.title, 'priority': tag.priority,
        'highlighted': tag.highlighted, 'expires_at': tag.expires_at}
        for tag in MemberTag.objects.filter(account=account, is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))]
    stats = Order.objects.filter(customer_account=account, business_cycle=account.business_cycle, is_gift_snapshot=False, consumption_confirmed_at__isnull=False, status='COMPLETED', payment_status='PAID').aggregate(amount=Sum('amount'), count=Count('id'))
    for item in Achievement.objects.filter(is_active=True):
        metric = (stats['amount'] or 0) if item.metric == 'amount' else (stats['count'] or 0)
        if metric >= item.target:
            tags.append({'key': f'achievement:{item.pk}', 'title': item.title,
                'priority': item.target if item.metric == 'amount' else 0,
                'highlighted': False, 'expires_at': None})
    return sorted(tags, key=lambda tag: (-tag['priority'], tag['key']))


def member_data(account):
    profile, _ = MemberProfile.objects.get_or_create(account=account)
    tags = available_tags(account)
    preferred = [tag for key in profile.selected_tags for tag in tags if tag['key'] == key]
    recharge = RechargeRecord.objects.filter(account=account).aggregate(amount=Sum('amount'))['amount'] or 0
    consumption = completed_consumption(account)
    tiers = VipTier.objects.select_related('boss_type').filter(active=True, boss_type__is_active=True).order_by('level')
    current = next((tier for tier in tiers if tier.boss_type_id == account.boss_type_id), None)
    upcoming = next((tier for tier in tiers if tier.level > (current.level if current else 0) and tier.recharge_threshold > consumption), None)
    boss_type = account.boss_type
    return {'points': profile.points_balance / 100, 'birthday': profile.birthday,
        'birthday_public': profile.birthday_public, 'selected_tags': profile.selected_tags,
        'tags': tags, 'display_tags': preferred or tags[:3], 'recharge_amount': recharge,
        'consumption_amount': consumption, 'upgrade_basis':'CONFIRMED_CONSUMPTION',
        'business_cycle': account.business_cycle,
        'pending_consumption_amount': Order.objects.filter(customer_account=account, business_cycle=account.business_cycle, status='COMPLETED', payment_status='PAID', is_gift_snapshot=False, consumption_confirmed_at__isnull=True).aggregate(value=Sum('amount'))['value'] or 0,
        'boss_type': {'id':boss_type.pk, 'name':boss_type.name, 'discount_rate':boss_type.discount_rate} if boss_type else None,
        'vip': TierSerializer(current).data if current else None,
        'next_vip': TierSerializer(upcoming).data if upcoming else None,
        'tiers': TierSerializer(tiers, many=True).data}


class MemberView(APIView):
    permission_classes = [IsBoss]
    def get(self, request):
        return Response({'code': 0, 'data': member_data(request.account)})
    @transaction.atomic
    def patch(self, request):
        serializer = MemberEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = locked_profile(request.account)
        data = serializer.validated_data
        if 'birthday' in data and profile.birthday and data['birthday'] != profile.birthday:
            raise serializers.ValidationError('生日已登记，修改请联系客服核对')
        if 'selected_tags' in data:
            allowed = {tag['key'] for tag in available_tags(request.account)}
            if any(key not in allowed for key in data['selected_tags']):
                raise serializers.ValidationError('只能展示自己已获得且未到期的标签')
        for key, value in data.items():
            setattr(profile, key, value)
        profile.save()
        if data.get('birthday_public') is False:
            Broadcast.objects.filter(account=request.account, kind='BIRTHDAY').update(is_visible=False)
        return Response({'code': 0, 'data': member_data(request.account)})


class HomeFeedView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    def get(self, request):
        now = timezone.now()
        qs = Broadcast.objects.filter(is_visible=True, published_at__isnull=False, starts_at__lte=now, expires_at__gt=now).order_by('-id')
        kind = request.query_params.get('kind', '')
        if kind:
            if kind == 'LOTTERY':
                qs = qs.filter(kind__startswith='LOTTERY')
            else:
                qs = qs.filter(kind=kind)
        page = serializers.IntegerField(min_value=1, max_value=10000).run_validation(request.query_params.get('page', 1))
        # Cursor pagination keeps realtime inserts from shifting older pages.
        before = request.query_params.get('before')
        if before:
            qs = qs.filter(pk__lt=serializers.IntegerField(min_value=1).run_validation(before))
        rows = list(qs[:31] if before else qs[(page - 1) * 30:page * 30 + 1])
        items = [{'id': row.pk, 'kind': row.kind, 'content': row.content, 'created_at': row.created_at} for row in rows[:30]]
        if 'page' in request.query_params or before:
            return Response({'code': 0, 'data': {'items': items, 'has_more': len(rows) > 30, 'next': rows[29].pk if len(rows) > 30 else None}})
        return Response({'code': 0, 'data': items})


class ContentView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, pk=None):
        now = timezone.now()
        qs = ClubContent.objects.filter(is_active=True).exclude(kind='POPUP').filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        if pk:
            # 已发布活动到期后仍能从活动记录查看，未发布及下架内容不公开。
            qs = qs.filter(Q(kind='HOT') | Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            return Response({'code': 0, 'data': ContentSerializer(get_object_or_404(qs, pk=pk), context={"display_images": True}).data})
        if request.query_params.get('kind') == 'HOT':
            qs = qs.filter(kind='HOT').annotate(published_at=Coalesce('starts_at', 'created_at')).order_by('-published_at', '-id')
            if request.query_params.get('archive') != '1':
                qs = qs.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            page = serializers.IntegerField(min_value=1, max_value=10000).run_validation(request.query_params.get('page', 1))
            return Response({'code': 0, 'data': ContentSerializer(qs[(page - 1) * 30:page * 30], many=True, context={"display_images": True}).data})
        qs = qs.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        return Response({'code': 0, 'data': ContentSerializer(qs[:50], many=True, context={"display_images": True}).data})


class PointsView(APIView):
    permission_classes = [IsPointsMember]
    def get(self, request):
        profile, _ = MemberProfile.objects.get_or_create(account=request.account)
        qs = PointLedger.objects.filter(account=request.account)
        before = request.query_params.get('before')
        if before:
            before = serializers.IntegerField(min_value=1).run_validation(before)
            qs = qs.filter(id__lt=before)
        rows = list(qs[:30])
        return Response({'code': 0, 'data': {'points': profile.points_balance / 100,
            'list': [{'id': row.pk, 'kind': row.kind, 'amount': row.amount / 100,
                'balance_after': row.balance_after / 100, 'remark': row.remark, 'created_at': row.created_at} for row in rows],
            'next': rows[-1].pk if len(rows) == 30 else None}})


class RewardsView(APIView):
    permission_classes = [IsPointsMember]
    def get(self, request):
        rows = RewardItem.objects.filter(is_active=True, in_shop=True,audience__in=['ALL',request.account.account_type])
        return Response({'code': 0, 'data': RewardSerializer(rows, many=True).data})


class RedeemView(APIView):
    permission_classes = [IsPointsMember]
    def post(self, request):
        reward_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('reward_id'))
        get_object_or_404(RewardItem, pk=reward_id)
        details = ClaimDetailsSerializer(data=request.data.get('details', {}))
        details.is_valid(raise_exception=True)
        claim = redeem(request.account, reward_id, request.data.get('client_request_id'), details.validated_data)
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})


class ClaimsView(APIView):
    permission_classes = [IsPointsMember]
    def get(self, request, pk=None):
        if pk:
            claim = get_object_or_404(RewardClaim, pk=pk, account=request.account)
            return Response({'code': 0, 'data': ClaimSerializer(claim).data})
        rows = RewardClaim.objects.filter(account=request.account).select_related('account')[:100]
        return Response({'code': 0, 'data': ClaimSerializer(rows, many=True).data})
    @transaction.atomic
    def patch(self, request, pk):
        claim = get_object_or_404(RewardClaim.objects.select_for_update(), pk=pk, account=request.account)
        if claim.status != 'PENDING':
            raise serializers.ValidationError('交付已开始，修改资料请联系客服')
        details = ClaimDetailsSerializer(data=request.data.get('details', {}))
        details.is_valid(raise_exception=True)
        claim.details = details.validated_data
        claim.save(update_fields=['details'])
        return Response({'code': 0, 'data': ClaimSerializer(claim).data})


class LotteryView(APIView):
    permission_classes = [IsBoss]
    def get(self, request):
        campaign = LotteryCampaign.objects.order_by('-id').first()
        return Response({'code': 0, 'data': CampaignSerializer(campaign).data if campaign else None})
    def post(self, request):
        campaign_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('campaign_id'))
        version = serializers.IntegerField(min_value=1).run_validation(request.data.get('version'))
        get_object_or_404(LotteryCampaign, pk=campaign_id)
        result = draw(request.account, campaign_id, request.data.get('client_request_id'), version)
        return Response({'code': 0, 'data': {'id': result.pk, 'prize_id': result.prize_id,
            'point_cost': result.point_cost, 'claim': ClaimSerializer(result.claim).data}})
