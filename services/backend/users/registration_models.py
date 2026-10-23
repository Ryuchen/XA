import uuid
from pathlib import PurePath

from django.db import models


def registration_image_path(instance, filename):
    suffix = PurePath(filename).suffix.lower()
    return f'provider_registration/{instance.pk or "new"}/{uuid.uuid4().hex}{suffix}'


class ProviderRegistration(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '待审核'
        APPROVED = 'APPROVED', '已通过'
        REJECTED = 'REJECTED', '需补充资料'

    username = models.CharField(max_length=32, unique=True)
    phone = models.CharField(max_length=11, unique=True)
    password_hash = models.CharField(max_length=128)
    real_name = models.CharField(max_length=50)
    id_number_ciphertext = models.TextField()
    id_number_hash = models.CharField(max_length=64, unique=True)
    id_number_last4 = models.CharField(max_length=4)
    display_name = models.CharField(max_length=50)
    id_front = models.ImageField(upload_to=registration_image_path)
    id_back = models.ImageField(upload_to=registration_image_path)
    deposit_proof = models.ImageField(upload_to=registration_image_path, blank=True)
    deposit_mode = models.CharField(max_length=16, choices=[('CASH', '现金押金'), ('EARNINGS', '单抵押金')], default='CASH')
    deposit_required = models.PositiveIntegerField(default=20000)
    deposit_paid = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    revision = models.PositiveIntegerField(default=1)
    account = models.OneToOneField(
        'club_accounts.ClubAccount', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='provider_registration',
    )
    reviewer = models.ForeignKey(
        'club_accounts.ClubAccount', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reviewed_provider_registrations',
    )
    level = models.ForeignKey('users.EscortLevel', null=True, blank=True, on_delete=models.SET_NULL)
    review_note = models.CharField(max_length=500, blank=True, default='')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'APPROVED', 'REJECTED']),
                name='provider_registration_status_valid',
            ),
        ]
