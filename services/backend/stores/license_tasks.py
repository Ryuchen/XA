"""Platform-wide scanner; store context is set explicitly for each delivery."""
from celery import Task, shared_task


@shared_task(base=Task, name='stores.license_tasks.deliver_license_notices', ignore_result=True)
def deliver_license_notices():
    from django.conf import settings
    if not getattr(settings, 'LICENSE_NOTICE_DISPATCH_ENABLED', True):
        return 0
    from .certificate_notices import collect_all_certificate_notices
    from .license_notices import collect_all_notices, drain_notices
    collect_all_notices()
    collect_all_certificate_notices()
    return drain_notices()
