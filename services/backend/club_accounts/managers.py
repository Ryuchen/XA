from django.db import models


class ClubAccountManager(models.Manager):
    def create_account(self, username, password=None, **extra_fields):
        username = (username or '').strip()
        if not username:
            raise ValueError('业务账号不能为空')

        account = self.model(username=username, **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account
