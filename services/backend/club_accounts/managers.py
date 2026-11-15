from django.db import models


class ClubAccountQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        from .license_quota import enabled, LicenseAdmissionError
        if any(obj.account_type in ('BOSS', 'PROVIDER') for obj in objs):
            if enabled():
                raise LicenseAdmissionError('老板和陪玩账号须逐个核验门店授权')
            if kwargs.get('ignore_conflicts') or kwargs.get('update_conflicts'):
                raise LicenseAdmissionError('账号批量创建不支持忽略或覆盖冲突')
            from django.db import transaction
            with transaction.atomic(using=self.db):
                from .license_quota import lock_category
                for account_type in sorted({obj.account_type for obj in objs} & {'BOSS', 'PROVIDER'}):
                    lock_category(account_type)
                for obj in objs:
                    obj.save(using=self.db, force_insert=True)
            return objs
        from django.db import transaction
        from .models import ProviderLifecycleLock
        using = self.db
        with transaction.atomic(using=using):
            result = super().bulk_create(objs, **kwargs)
            provider_numbers = [obj.account_no for obj in objs if obj.account_type == 'PROVIDER']
            if provider_numbers:
                ids = self.model.objects.using(using).filter(
                    account_type='PROVIDER', account_no__in=provider_numbers).values_list('pk', flat=True)
                ProviderLifecycleLock.objects.using(using).bulk_create(
                    [ProviderLifecycleLock(account_id=pk) for pk in ids], ignore_conflicts=True)
            return result


class ClubAccountManager(models.Manager.from_queryset(ClubAccountQuerySet)):
    def create_account(self, username, password=None, **extra_fields):
        username = (username or '').strip()
        if not username:
            raise ValueError('业务账号不能为空')

        account = self.model(username=username, **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account
