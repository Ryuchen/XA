"""Short dispatch hints that preserve the context of reservation orders."""

from .models import Reservation


def service_arrangement_hint(order):
    if Reservation.objects.filter(order_id=order.pk).exists():
        return '请查看原预约时段并与老板确认服务安排'
    return '请尽快开始服务'
