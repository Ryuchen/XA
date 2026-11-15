"""后台异步任务。

- lift_expired_bans_task：扫描到期封禁并自动解封（Celery beat 定时调用）。
"""
from celery import shared_task

from .ban_utils import lift_expired_bans


@shared_task
def recover_separation_performance_waivers():
    """Reconcile post-separation refunds in each tenant, even without re-employment."""
    import logging
    from django.conf import settings
    from stores.db import transaction
    from .payroll_calculation import refund_accounting_enabled
    from .payroll_models import PayrollSeparationSettlement
    from .payroll_event_barrier import lock_payroll_events
    from .payroll_refund_ledger import capture_separation_performance_waivers
    from .payroll_reemployment_refund_waiver import capture_reemployment_refund_waivers
    if not refund_accounting_enabled() or not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        return 0
    count = 0
    ids = PayrollSeparationSettlement.objects.order_by('pk').values_list('pk', flat=True)
    for pk in ids.iterator(chunk_size=100):
        for recover in (capture_separation_performance_waivers, capture_reemployment_refund_waivers):
            try:
                with transaction.atomic():
                    lock_payroll_events()
                    separation = PayrollSeparationSettlement.objects.select_related('final_run').get(pk=pk)
                    recovered = recover(separation)
                count += recovered
            except Exception:
                logging.getLogger(__name__).exception('离职绩效免追待重试 settlement_id=%s handler=%s',
                                                       pk, recover.__name__)
    return count


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
