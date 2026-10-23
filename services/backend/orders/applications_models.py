from django.db import models

class OrderApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '等待老板选择'
        SELECTED = 'SELECTED', '已选中'
        NOT_SELECTED = 'NOT_SELECTED', '未选中'
        WITHDRAWN = 'WITHDRAWN', '已撤回'
    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='applications')
    applicant = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='order_applications')
    partner = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='team_applications', null=True, blank=True)
    partner_confirmed = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order','applicant'], name='uniq_order_application'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING','SELECTED','NOT_SELECTED','WITHDRAWN']), name='application_status_valid'),
            models.CheckConstraint(condition=models.Q(partner__isnull=True) | ~models.Q(partner=models.F('applicant')), name='application_partner_distinct'),
        ]
        ordering = ['created_at','id']
