"""陪玩师评分聚合：按真实评价重算，避免逐次四舍五入造成偏差。

评分聚合的单一来源，供 C 端提交评价与后台删除评价复用。
调用方需自行包裹 transaction.atomic()。
"""

from users.models import EscortProfile
from decimal import Decimal
from django.db.models import Count, Q, Sum


def _rating_profile(provider_id, provider_account_id):
    profiles = EscortProfile.objects.select_for_update()
    if provider_account_id:
        profile = profiles.filter(account_id=provider_account_id).first()
        if profile:
            return profile
        # Old profiles may not yet carry their business account. Only use the
        # legacy user if its permanent mapping identifies the same account.
        from club_accounts.models import LegacyAccountMap
        legacy_id = LegacyAccountMap.objects.filter(account_id=provider_account_id).values_list('legacy_user_id', flat=True).first()
        return profiles.filter(user_id=legacy_id, account__isnull=True).first() if legacy_id else None
    return profiles.filter(user_id=provider_id).first() if provider_id else None


def escort_rating_summary(profile, *, provider_account_id=None, exclude_evaluation_id=None):
    """Return the number and average of reviews belonging to this profile."""
    from club_accounts.models import LegacyAccountMap
    from .models import Evaluation

    mapping = LegacyAccountMap.objects.filter(legacy_user_id=profile.user_id).first()
    account_id = provider_account_id or profile.account_id or (mapping.account_id if mapping else None)
    if account_id:
        ownership = Q(provider_account_id=account_id)
        mapped_legacy_id = LegacyAccountMap.objects.filter(account_id=account_id).values_list('legacy_user_id', flat=True).first()
        if mapped_legacy_id:
            ownership |= Q(provider_account__isnull=True, provider_id=mapped_legacy_id)
    else:
        ownership = Q(provider_account__isnull=True, provider_id=profile.user_id)
    evaluations = Evaluation.objects.filter(ownership)
    if exclude_evaluation_id is not None:
        evaluations = evaluations.exclude(pk=exclude_evaluation_id)
    summary = evaluations.aggregate(count=Count('id'), total=Sum('score'))
    count = summary['count']
    average = (Decimal(summary['total']) / Decimal(count)).quantize(Decimal('0.01')) if count else Decimal('0.00')
    return count, average


def refresh_escort_rating(provider_id: int, *, provider_account_id=None, exclude_evaluation_id=None) -> None:
    """With the profile locked, derive its current rating from its evaluations."""
    profile = _rating_profile(provider_id, provider_account_id)
    if not profile:
        return
    profile.rating_count, profile.rating_avg = escort_rating_summary(
        profile, provider_account_id=provider_account_id, exclude_evaluation_id=exclude_evaluation_id,
    )
    profile.save(update_fields=['rating_avg', 'rating_count'])
