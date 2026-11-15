"""Availability check for staged per-store deposit evidence migration."""
from stores.db import connection
from .models import DepositPaymentSnapshot


def snapshot_storage_available():
    # A newly deployed application may reach a store before its migration is
    # applied. Never create an untraceable payment or query a missing table.
    return DepositPaymentSnapshot._meta.db_table in connection.introspection.table_names()
