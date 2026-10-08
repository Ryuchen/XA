from django.db import models


def default_limits():
    # 每账号/分钟、全站/分钟、全站/秒；报价与刷新独立于交易配额。
    rates = {
        'valley': [(12, 120, 12), (30, 300, 30), (30, 600, 60), (60, 1200, 120)],
        'normal': [(10, 240, 24), (24, 600, 60), (24, 1200, 120), (45, 2400, 240)],
        'peak': [(6, 300, 30), (18, 900, 90), (18, 1800, 180), (30, 3600, 360)],
    }
    return {mode: {action: dict(zip(('account_per_minute', 'global_per_minute', 'global_per_second'), values))
                   for action, values in zip(('create', 'grab', 'pool', 'quote'), rows)} for mode, rows in rates.items()}


class OrderTrafficConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=True)
    self_order_enabled = models.BooleanField(default=True)
    peak_start = models.PositiveSmallIntegerField(default=18)
    peak_end = models.PositiveSmallIntegerField(default=24)
    valley_start = models.PositiveSmallIntegerField(default=0)
    valley_end = models.PositiveSmallIntegerField(default=8)
    limits = models.JSONField(default=default_limits)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
