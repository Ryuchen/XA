from django.db import models


class OrdinaryTransferAddonAgreement(models.Model):
    """Add-on responsibilities presented with one negotiation proposal."""
    proposal = models.OneToOneField('orders.NegotiationProposal', on_delete=models.PROTECT,
                                    related_name='ordinary_transfer_addon_agreement')
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
