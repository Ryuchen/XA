"""Create isolated Docker verification credentials without printing or replacing them."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
destination = root / '.tools/local-validation.env'
destination.parent.mkdir(exist_ok=True)
if destination.exists():
    print('Local verification environment already exists; credentials unchanged.')
else:
    entries = {
        'COMPOSE_PROJECT_NAME': 'xa-local-validation',
        'DOCKERHUB_NAMESPACE': 'xa-local',
        'XA_IMAGE_TAG': 'validation',
        'DJANGO_SECRET_KEY': secrets.token_urlsafe(48),
        'DJANGO_ALLOWED_HOSTS': '127.0.0.1,localhost,web',
        'MYSQL_ROOT_PASSWORD': secrets.token_urlsafe(32),
        'MYSQL_USER': 'xa_verify',
        'MYSQL_PASSWORD': secrets.token_urlsafe(32),
        'MYSQL_DATABASE': 'xa_verify',
        'WECHAT_MOCK_LOGIN': 'false',
        'KOOK_ENABLED': 'false',
        'WECOM_DISPATCH_ENABLED': 'false',
    }
    with destination.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('# Local Docker verification only. No production or external-platform credentials.\n')
        stream.writelines(f'{name}={value}\n' for name, value in entries.items())
    destination.chmod(0o600)
    print('Created .tools/local-validation.env; private values were not printed.')
