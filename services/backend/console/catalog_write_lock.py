"""One store-local transaction boundary for catalogue configuration."""
from stores.db import transaction
from django.db import DatabaseError
from rest_framework.exceptions import APIException

from .models import CatalogWriteLock


class CatalogUnavailable(APIException):
    status_code = 503
    default_detail = '门店目录暂不可编辑，请稍后重试或联系平台'
    default_code = 'CATALOG_UNAVAILABLE'


def lock_catalog():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('商品目录修改必须在门店事务内进行')
    try:
        CatalogWriteLock.objects.select_for_update().get(pk=1)
    except (DatabaseError, CatalogWriteLock.DoesNotExist) as exc:
        raise CatalogUnavailable() from exc


class CatalogMutationMixin:
    """Lock before DRF reads or validates the editable catalogue record."""
    def create(self, request, *args, **kwargs):
        with transaction.atomic():
            lock_catalog()
            return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            lock_catalog()
            return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            lock_catalog()
            return super().destroy(request, *args, **kwargs)
