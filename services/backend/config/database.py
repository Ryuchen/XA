"""Database configuration shared by deployment and isolated validation."""
from pathlib import Path
import ssl

from django.core.exceptions import ImproperlyConfigured


def mysql_database(env):
    mode = env.get('XA_DATABASE_MODE', 'embedded')
    if mode not in {'embedded', 'managed'}:
        raise ImproperlyConfigured('XA_DATABASE_MODE 必须为 embedded 或 managed')
    host = env.get('MYSQL_HOST', '127.0.0.1')
    user = env.get('MYSQL_USER', 'root')
    password = env.get('MYSQL_PASSWORD', 'xa_dev_password')
    name = env.get('MYSQL_DATABASE', 'xa_db')
    ca = env.get('MYSQL_SSL_CA', '').strip()
    try:
        timeout = int(env.get('MYSQL_CONNECT_TIMEOUT', '10'))
        port = int(env.get('MYSQL_PORT', '3306'))
        if not 1 <= timeout <= 300 or not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise ImproperlyConfigured('MYSQL_CONNECT_TIMEOUT 需为 1–300 秒；MYSQL_PORT 需为有效端口') from None
    if mode == 'managed':
        if not host or host.lower() in {'db', 'localhost', '127.0.0.1', '::1'} or '://' in host or host != host.strip():
            raise ImproperlyConfigured('托管 MySQL 必须填写实际数据库主机 MYSQL_HOST')
        if not name or not user or user.lower() == 'root' or not password or password == 'xa_dev_password':
            raise ImproperlyConfigured('托管 MySQL 必须配置库名、非 root 应用账号及独立密码')
        if not ca:
            raise ImproperlyConfigured('托管 MySQL 必须配置 MYSQL_SSL_CA 并验证数据库身份')
    options = {'charset': 'utf8mb4', 'isolation_level': 'read committed', 'connect_timeout': timeout}
    if ca:
        try:
            if not Path(ca).is_file():
                raise ValueError()
            ssl.create_default_context(cafile=ca)
        except (OSError, ValueError, ssl.SSLError):
            raise ImproperlyConfigured('MYSQL_SSL_CA 必须为可读取的有效 CA 证书文件') from None
        options.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
    return {
        'ENGINE': 'config.mysql_tls' if ca else 'django.db.backends.mysql',
        'NAME': name, 'USER': user, 'PASSWORD': password, 'HOST': host, 'PORT': str(port),
        'OPTIONS': options,
    }
