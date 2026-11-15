"""Select the existing source ledger for ordinary service payments."""


def use_order_funding_allocation(*, amount, funding_selection_enabled):
    """Use the verified ledger whenever ordinary funded settlement is ready.

    The older default-principal ledger remains necessary while that settlement
    capability is closed.  This selector does not enable any funding feature.
    """
    if amount <= 0:
        return False
    from orders.points_checkout_test import ordinary_settlement_enabled
    return bool(funding_selection_enabled or ordinary_settlement_enabled())
