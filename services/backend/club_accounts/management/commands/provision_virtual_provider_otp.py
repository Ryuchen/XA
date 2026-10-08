import base64
import json
import re
import secrets
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from club_accounts.models import ClubAccount, OtpCredential
from club_accounts.otp import decrypt_secret, encrypt_secret, recovery_hash


def remaining_recovery_codes(credential, row, source):
    """Export only codes that are still valid; never replace them with another plan."""
    remaining = set(credential.recovery_hashes)
    for candidate in (row.get('otpRecoveryCodes'), source.get('otpRecoveryCodes')):
        if not isinstance(candidate, list):
            continue
        valid = []
        seen = set()
        for code in candidate:
            if not isinstance(code, str):
                continue
            hashed = recovery_hash(code)
            if hashed in remaining and hashed not in seen:
                valid.append(code)
                seen.add(hashed)
        if seen == remaining:
            return valid
    if not remaining:
        return []
    raise CommandError('交付清单缺少当前有效的恢复码，拒绝用旧计划覆盖；请恢复原交付清单')


class Command(BaseCommand):
    help = '仅为100个xa_virtual测试账号配置独立OTP密钥，真实陪玩/管理账号由本人首次登录绑定'

    def add_arguments(self, parser):
        parser.add_argument('--plan', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        rows = json.loads(Path(options['output']).read_text(encoding='utf-8'))
        plan = json.loads(Path(options['plan']).read_text(encoding='utf-8'))
        if len(rows) != 100 or len(plan) != 100:
            raise CommandError('仅支持本次100个测试账号清单')
        with transaction.atomic():
            for n, (row, source) in enumerate(zip(rows, plan), 1):
                username = f'xa_virtual_{n:03d}'
                if row.get('username') != username or source.get('username') != username:
                    raise CommandError('测试账号清单顺序不匹配')
                account = ClubAccount.objects.select_for_update().get(
                    pk=row['id'], username=username, account_type='PROVIDER',
                )
                if not account.check_password(source['password']):
                    raise CommandError('测试账号密码不匹配')
                credential = OtpCredential.objects.filter(account=account).first()
                secret = source.get('otpSecret') or base64.b32encode(secrets.token_bytes(20)).decode()
                if not isinstance(secret, str) or not re.fullmatch('[A-Z2-7]{32}', secret):
                    raise CommandError('测试OTP密钥格式不正确')
                if credential:
                    if source.get('otpSecret') != decrypt_secret(credential.secret_ciphertext):
                        raise CommandError('测试账号已绑定其他验证器，拒绝改写')
                    row['otpSecret'] = source['otpSecret']
                    row['otpRecoveryCodes'] = remaining_recovery_codes(credential, row, source)
                else:
                    codes = [secrets.token_hex(8).upper() for _ in range(8)]
                    codes = [code[:8] + '-' + code[8:] for code in codes]
                    OtpCredential.objects.create(
                        account=account, secret_ciphertext=encrypt_secret(secret),
                        recovery_hashes=[recovery_hash(code) for code in codes],
                    )
                    row['otpSecret'] = secret
                    row['otpRecoveryCodes'] = codes
            target = Path(options['output'])
            target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
            target.chmod(0o600)
        self.stdout.write('100个测试账号独立OTP配置完成，密钥仅写入交付清单')
