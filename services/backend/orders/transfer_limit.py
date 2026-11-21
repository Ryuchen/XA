"""Enforce two committed handoffs per purchased participant seat."""
from stores.db import transaction

from .models import OrderTransferSeat, OrderTransferSeatEvent
from .state_machine import IllegalTransitionError


def record_locked(order, commands):
    """Caller holds the order lock; all changes roll back with the transfer."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单上限必须在订单事务内核对')
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    if not {OrderTransferSeat._meta.db_table, OrderTransferSeatEvent._meta.db_table} <= tables:
        raise IllegalTransitionError('转单次数记录尚未就绪，请联系管理员')
    from .team_rules import required_team_size
    rows = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
    active = [row.provider_account_id for row in rows]
    if (len(active) != required_team_size(order) or len(set(active)) != len(active)
            or any(not account for account in active)):
        raise IllegalTransitionError('订单席位与陪玩记录不一致，请先核对')
    if (not commands or len({old for old, _ in commands}) != len(commands)
            or len({new for _, new in commands}) != len(commands)
            or any(type(old) is not int or type(new) is not int or old not in active or new in active
                   or old == new for old, new in commands)):
        raise IllegalTransitionError('转单席位或接替陪玩已变化，请刷新后核对')
    seats = list(OrderTransferSeat.objects.select_for_update().filter(order=order).order_by('slot'))
    if not seats:
        from .models import OrderOriginalShareTransfer, OrderPrestartHandoff, OrderStatusLog
        if (OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                or OrderPrestartHandoff.objects.filter(order=order).exists()
                or OrderStatusLog.objects.filter(order=order, action=OrderStatusLog.Action.TRANSFER).exists()):
            raise IllegalTransitionError('订单已有未核对转单记录，请先联系管理员')
        seats = [OrderTransferSeat.objects.create(order=order, slot=index, initial_account_id=account,
                                                  current_account_id=account)
                 for index, account in enumerate(active, start=1)]
    if len(seats) != len(active) or {seat.current_account_id for seat in seats} != set(active):
        raise IllegalTransitionError('订单转单席位记录异常，请先核对')
    for seat in seats:
        events = list(seat.events.select_for_update().order_by('sequence'))
        current = seat.initial_account_id
        for sequence, event in enumerate(events, start=1):
            if event.sequence != sequence or event.old_account_id != current or event.new_account_id == current:
                raise IllegalTransitionError('订单转单次数凭证异常，请先核对')
            current = event.new_account_id
        if len(events) != seat.transfer_count or current != seat.current_account_id:
            raise IllegalTransitionError('订单转单次数凭证异常，请先核对')
    by_account = {seat.current_account_id:seat for seat in seats}
    created_events = []
    for old, new in commands:
        seat = by_account[old]
        if seat.transfer_count >= 2:
            raise IllegalTransitionError('该席位已转单两次，不能继续更换陪玩')
        sequence = seat.transfer_count + 1
        created_events.append(OrderTransferSeatEvent.objects.create(
            seat=seat, sequence=sequence, old_account_id=old, new_account_id=new))
        seat.transfer_count = sequence; seat.current_account_id = new
        seat.save(update_fields=['transfer_count', 'current_account'])
    return created_events
