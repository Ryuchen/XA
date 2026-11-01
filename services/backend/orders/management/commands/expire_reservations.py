from django.core.management.base import BaseCommand

from orders.reservation_expiration import expire_due_reservations


class Command(BaseCommand):
    help = '将结束时间已到且尚未转单的预约标记为已过期'

    def handle(self, *args, **options):
        self.stdout.write(f'已过期 {expire_due_reservations()} 条预约')
