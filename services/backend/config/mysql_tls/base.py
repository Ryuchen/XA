import pymysql
from pymysql.constants import CLIENT
from django.db.backends.mysql.base import DatabaseWrapper as MySQLDatabaseWrapper
from django.utils.asyncio import async_unsafe


class TLSConnection(pymysql.connections.Connection):
    def _request_authentication(self):
        # PyMySQL normally falls back to plain transport when the server does
        # not advertise TLS. Refuse before transmitting authentication data.
        if not self.ssl or not self.server_capabilities & CLIENT.SSL:
            raise pymysql.err.OperationalError(2026, 'Database TLS is required')
        return super()._request_authentication()


class DatabaseWrapper(MySQLDatabaseWrapper):
    @async_unsafe
    def get_new_connection(self, conn_params):
        return TLSConnection(**conn_params)
