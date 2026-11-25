"""Closure guard adapter for the staff manual-order create action only."""


def manual_order_closure_refusal(request):
    from stores.closure import closure_refusal_response
    from stores.context import current_store
    # Host/token middleware owns tenant selection. Never accept a customer or
    # query parameter as authority for the store whose closure row is checked.
    return closure_refusal_response(current_store())
