"""后台异步任务。

- lift_expired_bans_task：扫描到期封禁并自动解封（Celery beat 定时调用）。
"""
from celery import shared_task

from .ban_utils import lift_expired_bans


@shared_task
def lift_expired_bans_task() -> str:
    """定时解除已到期的封禁。

    幂等：``lift_expired_bans`` 只处理仍为 ACTIVE 且 ``expires_at`` 已过的记录，
    重复执行不会二次翻转账户开关。

    Returns:
        str: 形如 ``lifted:3`` 的执行摘要，便于在 Celery 结果里直接看到效果。
    """
    count = lift_expired_bans()
    return f'lifted:{count}'
