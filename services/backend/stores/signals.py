from django.utils import timezone


def ensure_default_store(sender, using, **kwargs):
    from django.conf import settings
    if using != 'default' or getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
        return
    from .models import Store
    Store.objects.get_or_create(slug='shenglang', defaults={
        'name': '声浪电竞', 'database_alias': 'default',
        'status': Store.Status.ACTIVE, 'initialized_at': timezone.now(),
    })
