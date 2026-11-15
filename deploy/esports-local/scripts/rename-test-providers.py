"""Rename only provider presentation fields in the two registered test stores.

Audit by default. Use --apply after reviewing the plan. No usernames, passwords,
roles, real-name fields, IDs, money, or authentication state are changed.
"""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django
django.setup()
from django.conf import settings
from django.db import transaction
from users.models import CustomUser, EscortProfile
from users.registration_models import ProviderRegistration
from club_accounts.models import ClubAccount
from orders.models import Order, OrderProvider, Reservation

EXPECTED = {
    'default': 'xa_test_fronted_acceptance_20261104',
    'store_b': 'xa_test_store_preview_b_20261104',
}
SHORT_NAMES = [
    '林曦', '苏航', '顾言', '周砚', '叶舟', '知夏', '洛宁', '予安',
    '程野', '陆离', '沈星', '白露', '江枫', '雨桐', '以宁', '乐潼',
]
SURNAMES = ['林', '苏', '顾', '周', '叶', '陆', '沈', '江', '陈']
GIVEN_NAMES = [
    '听晚', '映雪', '予安', '清让', '知远', '栖迟', '星河', '叙白',
    '亦舟', '念安', '南笙', '景行', '清和', '云深', '星辞', '南洲',
    '月白', '青岚', '以宁', '乐潼',
]
NAMES = SHORT_NAMES + [surname + given for surname in SURNAMES for given in GIVEN_NAMES]
assert len(NAMES) == len(set(NAMES))
assert all(re.fullmatch(r'[\u4e00-\u9fff]{2,3}', name) for name in NAMES)

for alias, database in EXPECTED.items():
    config = settings.DATABASES[alias]
    assert config['NAME'] == database and config['HOST'] == '127.0.0.1' and str(config['PORT']) == '13306', alias


def unique_old_name_map(rows):
    result = {}
    ambiguous = set()
    for old_name, new_name in rows:
        if not old_name:
            continue
        if old_name in result and result[old_name] != new_name:
            ambiguous.add(old_name)
        else:
            result[old_name] = new_name
    return {key: value for key, value in result.items() if key not in ambiguous}


def plan_store(alias):
    with transaction.atomic(using=alias):
        profiles = list(EscortProfile.objects.using(alias).select_for_update().select_related('user', 'account').order_by('id'))
        legacy_without = list(CustomUser.objects.using(alias).select_for_update().filter(role=CustomUser.Role.PROVIDER, escort_profile__isnull=True).order_by('username'))
        account_without = list(ClubAccount.objects.using(alias).select_for_update().filter(account_type=ClubAccount.AccountType.PROVIDER, escort_profile__isnull=True).order_by('username'))
        registrations = list(ProviderRegistration.objects.using(alias).select_for_update().order_by('id'))
        required = len(profiles) + len({row.username for row in legacy_without + account_without}) + len([row for row in registrations if not row.account_id])
        assert required <= len(NAMES), f'{alias}: not enough deterministic names: {required}'

        names_by_user = {}
        names_by_account = {}
        original_names = []
        cursor = 0
        for profile in profiles:
            name = NAMES[cursor]
            cursor += 1
            if profile.user_id:
                names_by_user[profile.user_id] = name
            if profile.account_id:
                names_by_account[profile.account_id] = name
            original_names.extend([
                (profile.display_name, name),
                (profile.user.nickname if profile.user_id else '', name),
                (profile.account.nickname if profile.account_id else '', name),
            ])
        supplemental_name_by_username = {}
        for account in sorted(legacy_without + account_without, key=lambda row: row.username):
            if account.username not in supplemental_name_by_username:
                supplemental_name_by_username[account.username] = NAMES[cursor]
                cursor += 1
            name = supplemental_name_by_username[account.username]
            if isinstance(account, CustomUser):
                names_by_user[account.id] = name
            else:
                names_by_account[account.id] = name
            original_names.append((account.nickname, name))
        registration_names = {}
        for registration in registrations:
            if registration.account_id and registration.account_id in names_by_account:
                registration_names[registration.id] = names_by_account[registration.account_id]
                continue
            name = NAMES[cursor]
            cursor += 1
            registration_names[registration.id] = name
            if registration.account_id:
                names_by_account[registration.account_id] = name
            original_names.append((registration.display_name, name))

        old_name_map = unique_old_name_map(original_names)
        snapshot_changes = {Order: [], OrderProvider: [], Reservation: []}
        unmatched = []
        for model in (Order, OrderProvider, Reservation):
            for row in model.objects.using(alias).select_for_update().all():
                new_name = names_by_account.get(row.provider_account_id) or names_by_user.get(row.provider_id)
                if not new_name:
                    new_name = old_name_map.get(row.provider_name_snapshot)
                if new_name and row.provider_name_snapshot != new_name:
                    snapshot_changes[model].append((row, new_name))
                elif not new_name and row.provider_name_snapshot:
                    unmatched.append({'model': model.__name__, 'id': row.id, 'name': row.provider_name_snapshot})

        return {
            'alias': alias,
            'profiles': profiles,
            'legacy_without': legacy_without,
            'account_without': account_without,
            'registrations': registrations,
            'registration_names': registration_names,
            'names_by_user': names_by_user,
            'names_by_account': names_by_account,
            'snapshot_changes': snapshot_changes,
            'unmatched': unmatched,
        }


def apply_store(plan):
    alias = plan['alias']
    backup = {'alias': alias, 'profiles': [], 'legacy_users': [], 'accounts': [], 'registrations': [], 'snapshots': []}
    with transaction.atomic(using=alias):
        for profile in plan['profiles']:
            name = plan['names_by_account'].get(profile.account_id) or plan['names_by_user'].get(profile.user_id)
            backup['profiles'].append({'id': profile.id, 'display_name': profile.display_name})
            EscortProfile.objects.using(alias).filter(pk=profile.pk).update(display_name=name)
            if profile.user_id:
                backup['legacy_users'].append({'id': profile.user_id, 'nickname': profile.user.nickname})
                CustomUser.objects.using(alias).filter(pk=profile.user_id).update(nickname=name)
            if profile.account_id:
                backup['accounts'].append({'id': profile.account_id, 'nickname': profile.account.nickname})
                ClubAccount.objects.using(alias).filter(pk=profile.account_id).update(nickname=name)
        for account in plan['legacy_without']:
            backup['legacy_users'].append({'id': account.id, 'nickname': account.nickname})
            CustomUser.objects.using(alias).filter(pk=account.pk).update(nickname=plan['names_by_user'][account.id])
        for account in plan['account_without']:
            backup['accounts'].append({'id': account.id, 'nickname': account.nickname})
            ClubAccount.objects.using(alias).filter(pk=account.pk).update(nickname=plan['names_by_account'][account.id])
        for registration in plan['registrations']:
            name = plan['registration_names'][registration.id]
            if not name:
                raise RuntimeError(f'{alias}: registration {registration.id} has no assigned name')
            backup['registrations'].append({'id': registration.id, 'display_name': registration.display_name})
            ProviderRegistration.objects.using(alias).filter(pk=registration.pk).update(display_name=name)
        for model, rows in plan['snapshot_changes'].items():
            for row, new_name in rows:
                backup['snapshots'].append({'model': model.__name__, 'id': row.id, 'provider_name_snapshot': row.provider_name_snapshot})
                model.objects.using(alias).filter(pk=row.pk).update(provider_name_snapshot=new_name)
    return backup


def main():
    apply = '--apply' in sys.argv[1:]
    plans = [plan_store(alias) for alias in EXPECTED]
    report = []
    for plan in plans:
        report.append({
            'alias': plan['alias'],
            'profiles': len(plan['profiles']),
            'legacy_without_profile': len(plan['legacy_without']),
            'accounts_without_profile': len(plan['account_without']),
            'registrations': len(plan['registrations']),
            'snapshot_changes': {model.__name__: len(rows) for model, rows in plan['snapshot_changes'].items()},
            'unmatched': plan['unmatched'],
        })
    if not apply:
        print(json.dumps({'mode': 'audit', 'stores': report}, ensure_ascii=False, indent=2))
        return
    if any(plan['unmatched'] for plan in plans):
        raise SystemExit('Unmatched snapshots remain; review audit before applying')
    backups = [apply_store(plan) for plan in plans]
    backup_path = ROOT / '.tools' / 'test-provider-rename-backup.json'
    backup_path.write_text(json.dumps(backups, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'mode': 'applied', 'backup': str(backup_path), 'stores': report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
