"""Capture recipients from the locked reservation transition itself."""


def cancellation_intents(reservation, operator, reason):
    preview = f'预约 {reservation.reservation_no} 已取消' + (f'：{reason}' if reason else '')
    actor_id = operator.pk if operator else None
    staff = bool(operator and operator.account_type == 'STAFF')
    targets = []
    if staff or actor_id != reservation.customer_account_id:
        targets.append((reservation.customer_id, reservation.customer_account_id,
                        reservation.customer_business_cycle, 'reservation-detail'))
    if reservation.required_provider_count == 2:
        seats = reservation.seats.select_for_update().filter(provider_account__isnull=False).order_by('number')
        targets.extend((seat.provider_id, seat.provider_account_id, seat.provider_business_cycle, 'reservations')
                       for seat in seats if seat.provider_account_id != actor_id)
    elif reservation.provider_account_id != actor_id:
        targets.append((reservation.provider_id, reservation.provider_account_id,
                        reservation.provider_business_cycle, 'reservations'))
    return [{'user_id': user_id, 'account_id': account_id, 'business_cycle': cycle,
             'account_type': 'BOSS' if page == 'reservation-detail' else 'PROVIDER',
             'title': '预约已取消', 'preview': preview,
             'action_url': f'/pages/{page}/index?id={reservation.pk}'}
            for user_id, account_id, cycle, page in targets]


def expiration_intents(reservation, from_status):
    team_pending = reservation.required_provider_count == 2 and from_status == 'PENDING'
    preview = (f'预约 {reservation.reservation_no} 已过期' if team_pending else
               f'预约 {reservation.reservation_no} 已过期，如需服务请重新预约')
    targets = [(reservation.customer_id, reservation.customer_account_id,
                reservation.customer_business_cycle, 'reservation-detail')]
    if reservation.required_provider_count == 2:
        seats = reservation.seats.select_for_update().filter(provider_account__isnull=False).order_by('number')
        targets.extend((seat.provider_id, seat.provider_account_id, seat.provider_business_cycle, 'reservations') for seat in seats)
    else:
        targets.append((reservation.provider_id, reservation.provider_account_id,
                        reservation.provider_business_cycle, 'reservations'))
    return [{'user_id': user_id, 'account_id': account_id, 'business_cycle': cycle,
             'account_type': 'BOSS' if page == 'reservation-detail' else 'PROVIDER',
             'title': '预约已过期', 'preview': preview,
             'action_url': f'/pages/{page}/index?id={reservation.pk}'}
            for user_id, account_id, cycle, page in targets]


def conversion_intents(reservation, operator):
    from site_messages.order_cycle import recipient_cycle
    order = reservation.order
    if not order:
        return []
    targets = []
    if operator and operator.account_type == 'STAFF':
        targets.append({'user_id': reservation.customer_id, 'account_id': order.customer_account_id,
            'business_cycle': order.business_cycle, 'account_type': 'BOSS', 'title': '预约已转为订单',
            'preview': f'客服已将预约 {reservation.reservation_no} 转为订单，请查看详情',
            'action_url': f'/pages/reservation-detail/index?id={reservation.pk}'})
    providers = (list(order.providers.values_list('provider_id', 'provider_account_id'))
                 if reservation.required_provider_count == 2 else [(order.provider_id, order.provider_account_id)])
    for user_id, account_id in providers:
        cycle = recipient_cycle(order, account_id)
        if (cycle is None and reservation.required_provider_count == 1
                and account_id == reservation.provider_account_id and user_id == reservation.provider_id
                and not order.providers.exists()):
            from .models import OrderParticipationSegment
            if not OrderParticipationSegment.objects.filter(attribution__order=order).exists():
                cycle = reservation.provider_business_cycle
        targets.append({'user_id': user_id, 'account_id': account_id,
            'business_cycle': cycle, 'account_type': 'PROVIDER',
            'title': '预约已转为订单',
            'preview': f'预约「{order.service_name_snapshot}」已成单，请查看原预约时段并与老板确认服务安排',
            'related_order_id': order.pk, 'assignment_alert': True,
            'action_url': f'/pages/orderList/index?orderId={order.pk}'})
    return targets
