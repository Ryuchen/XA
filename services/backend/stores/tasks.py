"""Carry store context through queued jobs; fan out only known periodic scans."""
from celery import Task


PERIODIC_STORE_TASKS = frozenset({
    'orders.tasks.award_continuations',
    'orders.tasks.expire_reservations',
    'orders.tasks.recover_room_image_uploads',
    'orders.tasks.auto_confirm_room_orders',
    'orders.tasks.check_pending_timeouts',
    'orders.tasks.recover_pool_group_dispatch',
    'wallet.tasks.release_due_provider_income',
    'wallet.tasks.recover_withdrawal_notice_messages',
    'engagement.tasks.confirm_due_consumption',
    'engagement.tasks.retry_completion_subsidies',
    'engagement.tasks.publish_birthdays',
    'engagement.tasks.drain_broadcast_outbox',
    'engagement.tasks.grant_member_coupons',
    'engagement.tasks.retry_addon_vip_recalculations',
    'console.tasks.lift_expired_bans_task',
    'console.tasks.recover_separation_performance_waivers',
    'chat.tasks.drain_chat_notifications',
    'site_messages.tasks.drain_manual_messages',
    'site_messages.tasks.recover_checkin_notices',
    'site_messages.tasks.recover_reservation_invites',
    'site_messages.tasks.recover_reservation_status_notices',
    'site_messages.tasks.recover_reservation_seat_timeout_notices',
    'site_messages.tasks.recover_reservation_team_declined_notices',
    'site_messages.tasks.recover_reservation_team_replaced_notices',
    'site_messages.tasks.recover_reservation_event_notices',
    'site_messages.tasks.recover_audition_notices',
    'site_messages.tasks.recover_grab_result_notices',
    'site_messages.tasks.recover_grab_invites',
    'site_messages.tasks.recover_order_application_notices',
    'site_messages.tasks.recover_aftersale_closure_notices',
    'site_messages.tasks.recover_aftersale_open_notices',
    'site_messages.tasks.recover_evaluation_notices',
    'site_messages.tasks.recover_room_confirmation_notices',
    'site_messages.tasks.recover_room_review_notices',
    'site_messages.tasks.recover_room_objection_notices',
    'site_messages.tasks.recover_order_completion_notices',
    'site_messages.tasks.recover_order_timeout_notices',
    'site_messages.tasks.recover_customer_cancellation_notices',
    'site_messages.tasks.recover_order_refund_notices',
    'site_messages.tasks.recover_points_aftersale_refund_notices',
    'site_messages.tasks.recover_addon_refund_notices',
    'site_messages.tasks.recover_zero_refund_closure_notices',
    'site_messages.tasks.recover_addon_handover_result_notices',
    'site_messages.tasks.recover_service_transfer_notices',
    'site_messages.tasks.recover_negotiation_proposal_notices',
    'site_messages.tasks.recover_gift_receipt_notices',
    'site_messages.tasks.recover_staff_start_notices',
    'site_messages.tasks.recover_prestart_handoff_notices',
    'site_messages.tasks.recover_grab_team_result_notices',
    'site_messages.tasks.recover_grab_disqualification_notices',
    'site_messages.tasks.recover_order_assignment_notices',
    'site_messages.tasks.recover_addon_payment_notices',
    'site_messages.tasks.recover_addon_completion_notices',
    'site_messages.tasks.recover_addon_refund_proposal_notices',
    'site_messages.tasks.recover_addon_handover_proposal_notices',
    'site_messages.tasks.recover_deposit_event_notices',
    'site_messages.tasks.recover_deposit_application_notices',
    'site_messages.tasks.recover_recharge_receipt_notices',
    'site_messages.tasks.recover_recharge_gift_decision_notices',
})


def _validate_store_slug(slug):
    from .hosts import STORE_SLUG
    if not isinstance(slug, str) or not STORE_SLUG.fullmatch(slug):
        raise ValueError('门店业务任务的门店标识无效')


class StoreTask(Task):
    abstract = True

    def apply_async(self, args=None, kwargs=None, **options):
        from .context import current_store
        headers = dict(options.pop('headers', None) or {})
        store = current_store()
        if store is not None:
            if headers.get('store_slug') not in (None, store.slug):
                raise ValueError('Task store does not match the current store')
            headers['store_slug'] = store.slug
        elif 'store_slug' not in headers and self.name not in PERIODIC_STORE_TASKS:
            raise ValueError('门店业务任务必须指定所属门店')
        if 'store_slug' in headers:
            _validate_store_slug(headers['store_slug'])
        return super().apply_async(args=args, kwargs=kwargs, headers=headers, **options)

    def __call__(self, *args, **kwargs):
        from .context import current_store, resolve_store, store_context
        headers = self.request.headers or {}
        if 'store_slug' in headers:
            slug = headers['store_slug']
            _validate_store_slug(slug)
            store = resolve_store(slug, allow_cached=True)
            with store_context(store):
                return self.run(*args, **kwargs)
        if self.request.called_directly:
            if current_store() is not None:
                return self.run(*args, **kwargs)
        elif self.name in PERIODIC_STORE_TASKS:
            with store_context(None):
                return self._dispatch_periodic(args, kwargs)
        raise ValueError('门店业务任务必须指定所属门店')

    def _dispatch_periodic(self, args, kwargs):
        import logging
        from django.conf import settings
        from django.db import DatabaseError, InterfaceError, transaction
        from .context import active_stores

        # Each store receives its own message and retry history.
        directory_fallback = False
        try:
            with transaction.atomic(using='default'):
                stores = list(active_stores())
        except (DatabaseError, InterfaceError):
            if not getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
                raise
            from .routing_snapshot import cached_active_stores
            stores = cached_active_stores()
            if not stores:
                raise
            directory_fallback = True
        queued, enqueue_unknown = [], []
        for store in stores:
            try:
                result = self.apply_async(args=args, kwargs=kwargs, headers={'store_slug': store.slug})
            except Exception:
                # A broker error may occur after acceptance. Keep routing the other stores.
                logging.getLogger(__name__).warning(
                    'Periodic store task enqueue unconfirmed: task=%s store=%s', self.name, store.slug)
                enqueue_unknown.append(store.slug)
                continue
            queued.append({'store': store.slug, 'task_id': result.id})
        return {'stores': queued, 'enqueue_unknown': enqueue_unknown,
                'directory_fallback': directory_fallback}


# Register the platform-wide task without StoreTask's default-store routing.
from .license_tasks import deliver_license_notices  # noqa: E402,F401
from .withdrawal_audit_tasks import dispatch as dispatch_withdrawal_audits  # noqa: E402,F401
from .withdrawal_audit_tasks import scan_store as scan_withdrawal_audits  # noqa: E402,F401
