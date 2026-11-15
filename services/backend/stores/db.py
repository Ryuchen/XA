"""Django database APIs with tenant-aware defaults, without monkeypatching Django.

ORM routing alone does not route transaction.atomic(), on_commit(), or raw
connection cursors. Business code imports those three APIs from this module.
Explicit non-default aliases remain explicit, for migration/operations tools.
"""
import inspect
from contextlib import ContextDecorator
from functools import wraps

from django.db import connections as django_connections
from django.db import transaction as django_transaction

from .context import current_database, current_store, store_context


def _alias(using):
    return current_database() if using in (None, 'default') else using


class Connections:
    def __getitem__(self, alias):
        return django_connections[_alias(alias)]

    def __getattr__(self, name):
        return getattr(django_connections, name)


connections = Connections()


class Connection:
    def __getattr__(self, name):
        return getattr(django_connections[current_database()], name)

    def __setattr__(self, name, value):
        return setattr(django_connections[current_database()], name, value)


connection = Connection()


class Atomic(ContextDecorator):
    def __init__(self, using, savepoint, durable):
        self.using, self.savepoint, self.durable = using, savepoint, durable
        self._stack = []

    def _recreate_cm(self):
        return type(self)(self.using, self.savepoint, self.durable)

    def __enter__(self):
        atomic = django_transaction.atomic(using=_alias(self.using),
                                           savepoint=self.savepoint, durable=self.durable)
        self._stack.append(atomic)
        return atomic.__enter__()

    def __exit__(self, *args):
        return self._stack.pop().__exit__(*args)


class Transaction:
    def atomic(self, using=None, savepoint=True, durable=False):
        # A bare @transaction.atomic must resolve the context on invocation,
        # rather than capturing 'default' while importing the module.
        if callable(using):
            func = using

            @wraps(func)
            def decorated(*args, **kwargs):
                with self.atomic(savepoint=savepoint, durable=durable):
                    return func(*args, **kwargs)
            return decorated
        return Atomic(using, savepoint, durable)

    def on_commit(self, func, using=None, robust=False):
        store = current_store()

        @wraps(func)
        def in_context():
            with store_context(store):
                return func()
        return django_transaction.on_commit(in_context, using=_alias(using), robust=robust)

    def __getattr__(self, name):
        value = getattr(django_transaction, name)
        if not callable(value) or isinstance(value, type):
            return value
        signature = inspect.signature(value)
        if 'using' not in signature.parameters:
            return value

        @wraps(value)
        def routed(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            bound.arguments['using'] = _alias(bound.arguments.get('using'))
            return value(*bound.args, **bound.kwargs)
        return routed


transaction = Transaction()
