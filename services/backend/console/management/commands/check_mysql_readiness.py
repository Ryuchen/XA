"""Verify the database assumptions needed by dispatch locking and daily statistics."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = '检查正式 MySQL 的事务隔离、存储引擎和命名时区表（只读）'

    def handle(self, *args, **options):
        if connection.vendor != 'mysql':
            raise CommandError('此命令必须连接正式部署使用的 MySQL 数据库')
        errors = []
        with connection.cursor() as cursor:
            if connection.settings_dict.get('OPTIONS', {}).get('ssl_verify_identity'):
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                tls = cursor.fetchone()
                if not tls or not tls[1]:
                    errors.append('数据库连接未启用要求的 TLS 加密')
            cursor.execute('SELECT @@transaction_isolation, @@default_storage_engine')
            isolation, engine = cursor.fetchone()
            if isolation.upper().replace('-', ' ') != 'READ COMMITTED':
                errors.append('事务隔离级别必须为 READ COMMITTED，保证名额加锁后能读到已提交订单')
            if engine.upper() != 'INNODB':
                errors.append('默认存储引擎必须为 InnoDB')
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' AND ENGINE<>'InnoDB'"
            )
            if cursor.fetchall():
                errors.append('存在非 InnoDB 业务表，请完成事务存储引擎迁移')
            for stamp in (datetime(2026, 1, 1, 20), datetime(2026, 7, 1, 20)):
                expected = stamp.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.TIME_ZONE)).replace(tzinfo=None)
                cursor.execute("SELECT CONVERT_TZ(%s, 'UTC', %s)", (stamp, settings.TIME_ZONE))
                if cursor.fetchone()[0] != expected:
                    errors.append(f'MySQL 缺少或使用了过期的 UTC / {settings.TIME_ZONE} 时区表；按官方方式导入后重启数据库')
                    break
        if errors:
            raise CommandError('\n'.join(errors))
        self.stdout.write(self.style.SUCCESS('MySQL 验收通过：READ COMMITTED、InnoDB、命名时区转换正常'))
