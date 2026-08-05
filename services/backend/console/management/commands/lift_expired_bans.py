"""兜底命令：扫描到期封禁并自动解封。

用法：
    python manage.py lift_expired_bans

没有 Celery beat 的部署可加入 cron：
    */5 * * * * cd /path/to/backend && python manage.py lift_expired_bans
"""
from django.core.management.base import BaseCommand

from console.ban_utils import lift_expired_bans


class Command(BaseCommand):
    help = '扫描已到期仍生效的封禁并自动解封（落 EXPIRED 状态）'

    def handle(self, *args, **options):
        count = lift_expired_bans()
        self.stdout.write(
            self.style.SUCCESS(f'到期封禁扫描完成：已自动解封 {count} 条')
        )
