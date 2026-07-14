"""陪玩师评分聚合：新增评价时累加、删除评价时回退。

评分聚合的单一来源，供 C 端提交评价与后台删除评价复用。
调用方需自行包裹 transaction.atomic()。
"""

from users.models import EscortProfile


def apply_escort_rating(provider_id: int, score: int) -> None:
    """新增评价：将 score 累加进陪玩师评分聚合。"""
    if not provider_id:
        return
    profile = EscortProfile.objects.select_for_update().filter(user_id=provider_id).first()
    if not profile:
        return
    total = float(profile.rating_avg) * profile.rating_count + score
    profile.rating_count += 1
    profile.rating_avg = round(total / profile.rating_count, 2)
    profile.save(update_fields=['rating_avg', 'rating_count'])


def rollback_escort_rating(provider_id: int, score: int) -> None:
    """删除评价：将 score 从陪玩师评分聚合中回退。"""
    if not provider_id:
        return
    profile = EscortProfile.objects.select_for_update().filter(user_id=provider_id).first()
    if not profile or profile.rating_count <= 0:
        return
    remaining_count = profile.rating_count - 1
    if remaining_count <= 0:
        profile.rating_count = 0
        profile.rating_avg = 0
    else:
        total = float(profile.rating_avg) * profile.rating_count - score
        profile.rating_count = remaining_count
        profile.rating_avg = round(max(total, 0) / remaining_count, 2)
    profile.save(update_fields=['rating_avg', 'rating_count'])
