from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from lottery.engine import seed_catalog


class Command(BaseCommand):
    help = '幂等导入半周年活动3个奖池108格，不创建房间，不给老板发放次数。'

    def handle(self, *args, **options):
        try:
            count = seed_catalog()
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(f'已核对导入{count}个奖池、108个奖格；采用独立骰子及每池累计30/50次额外保底。'))
