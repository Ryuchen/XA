import io
import json
import os
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITestCase

from announcements.models import Announcement
from console.serializers import AdminServiceItemSerializer
from orders.management.commands.seed_club_catalog import CATALOG
from orders.catalog_initial_prices import approved_initial_prices
from orders.models import Order, ServiceItem
from orders.tests.factories import make_service, make_user
from wallet.models import Transaction, Wallet


@override_settings(DEBUG=True)
@skipUnless(connection.vendor == 'sqlite', '本地价目测试导入工具只允许 SQLite；其余业务测试在 MySQL 验证')
class ClubCatalogTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        cls.boss = make_user()

    def setUp(self):
        self.client.force_authenticate(self.boss)

    def find(self, prefix, platform='PC', level='TOP_SECRET'):
        return ServiceItem.objects.filter(name__startswith=prefix, platform=platform, security_level=level, is_active=True).order_by('price').first()

    def test_import_is_idempotent_and_does_not_touch_money_or_orders(self):
        before = list(ServiceItem.objects.order_by('id').values_list('id','catalog_key','price'))
        counts = [Order.objects.count(), Wallet.objects.count(), Transaction.objects.count()]
        call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        self.assertEqual(before,list(ServiceItem.objects.order_by('id').values_list('id','catalog_key','price')))
        self.assertEqual(counts,[Order.objects.count(), Wallet.objects.count(), Transaction.objects.count()])
        self.assertEqual(Announcement.objects.count(),4)

    def test_current_tables_match_24_regular_and_20_bank_tiers(self):
        regular = ServiceItem.objects.filter(name__startswith='常规物资单 ·',is_active=True)
        bank = ServiceItem.objects.filter(name__startswith='物资银行单 ·',is_active=True)
        self.assertEqual(regular.count(),24)
        self.assertEqual(bank.count(),20)
        for platform,prices in [('PC',[188,268,468,668,868,1668]),('MOBILE',[168,268,468,668,868,1068])]:
            for level in ('CONFIDENTIAL','TOP_SECRET'):
                self.assertEqual(list(regular.filter(platform=platform,security_level=level).order_by('price').values_list('price',flat=True)),[x*100 for x in prices])
        self.assertIn('3把内',self.find('物资银行单').description)
        self.assertIn('6把内',self.find('物资银行单',level='CONFIDENTIAL').description)

    def test_latest_revisions_and_decimal_prices_survive(self):
        self.assertEqual(self.find('新客体验单',platform='MOBILE').price,8800)
        self.assertIn('600w',self.find('千万才是开始',level='CONFIDENTIAL').description)
        self.assertIn('超额累计128w',self.find('千万才是开始',level='CONFIDENTIAL').description)
        self.assertIn('失败+2滴',self.find('清图版小小巨人').description)
        self.assertIn('500w',self.find('杀人如麻',level='CONFIDENTIAL').rules)
        self.assertIn('总目标50人',self.find('杀人如麻',level='CONFIDENTIAL').rules)
        self.assertEqual(ServiceItem.objects.get(name='咱俩练练单 · 娱乐SOLO',platform='PC').price,4880)
        self.assertEqual(ServiceItem.objects.get(name='部门任务 · 1→2级',platform='PC').price,150)

    def test_all_yuan_prices_convert_to_ten_coins_and_wrong_rate_is_rejected(self):
        book=json.loads(CATALOG.read_text(encoding='utf-8'))
        stored=dict(ServiceItem.objects.values_list('catalog_key','price'))
        initial_prices = approved_initial_prices(book['items'])
        for row in book['items']:
            expected = initial_prices.get(row['catalog_key'], row['price'])
            self.assertEqual(stored[row['catalog_key']], expected, row['name'])
            if row['catalog_key'] not in initial_prices:
                self.assertEqual(Decimal(expected) / 10, Decimal(row['price_yuan']) * 10, row['name'])
        book['items'][0]['price'] //= 10
        with patch('orders.management.commands.seed_club_catalog.CATALOG') as source:
            source.read_text.return_value=json.dumps(book)
            with self.assertRaisesMessage(CommandError, '1元应为10浪花'):
                call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        self.assertEqual(stored, dict(ServiceItem.objects.values_list('catalog_key','price')))

    def test_approved_initial_prices_and_reimport_preserve_store_edits(self):
        book = json.loads(CATALOG.read_text(encoding='utf-8'))
        original = {row['catalog_key']: row for row in book['items']}
        reference = approved_initial_prices(book['items'])
        self.assertEqual(sorted(reference.values()), [400, 4800, 4800, 6800, 6800, 10800])
        for key, price in reference.items():
            self.assertEqual(ServiceItem.objects.get(catalog_key=key).price, price)
        room_key = 'xa-price-817e8f1e23d0dd5ba034ab1c'
        self.assertEqual(original[room_key]['price'], 350)  # Historical record is retained.
        self.assertEqual(ServiceItem.objects.get(catalog_key=room_key).price, 400)
        # An unrelated running service retains its independently reviewed price.
        self.assertEqual(ServiceItem.objects.get(catalog_key='xa-price-a8699f65e25fcd38b66a56a9').price, 2000)
        prophet = ServiceItem.objects.get(catalog_key='xa-price-3871a8b2694a4d11bcad4ffe')
        self.assertIn('按本店预言家加购价', prophet.rules)
        self.assertNotIn('+58r', prophet.rules)
        for row in book['items']:
            if (row['platform'] == 'PC' and row['family'] in
                    ('环太平洋', '环太平洋预言家', '盲盒猜猜乐', '盲盒预言家')
                    and ('+58r' in row['rules'] or '+28r' in row['rules'])):
                saved = ServiceItem.objects.get(catalog_key=row['catalog_key'])
                self.assertNotIn('+58r', saved.rules)
                self.assertNotIn('+28r', saved.rules)
        prophet.price, prophet.rules = 7100, '门店自定玩法规则'
        prophet.save(update_fields=['price', 'rules'])
        call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        prophet.refresh_from_db()
        self.assertEqual((prophet.price, prophet.rules), (7100, '门店自定玩法规则'))

    def test_approved_override_rejects_wrong_source_item_without_writing(self):
        book = json.loads(CATALOG.read_text(encoding='utf-8'))
        item = next(row for row in book['items'] if row['catalog_key'] == 'xa-price-817e8f1e23d0dd5ba034ab1c')
        item['platform'] = 'MOBILE'
        before = list(ServiceItem.objects.order_by('pk').values_list('pk', 'price'))
        with patch('orders.management.commands.seed_club_catalog.CATALOG') as source:
            source.read_text.return_value = json.dumps(book)
            with self.assertRaisesMessage(CommandError, '初始参考价与历史价目不匹配'):
                call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        self.assertEqual(before, list(ServiceItem.objects.order_by('pk').values_list('pk', 'price')))

    def test_public_list_hides_drafts_and_exposes_units(self):
        rows=self.client.get('/api/orders/services/').data['data']
        book=json.loads(CATALOG.read_text(encoding='utf-8'))
        self.assertEqual(len(rows),sum(r['is_active'] for r in book['items']))
        self.assertFalse(any(r['name'].startswith('手游映射参考') for r in rows))
        self.assertIn('人·小时',{r['price_unit'] for r in rows})
        self.assertEqual(sum(r['is_gift'] for r in rows),6)

    def test_family_detail_keeps_all_price_variants_and_excludes_unrelated_items(self):
        service=self.find('常规物资单')
        family={'catalog_family':service.catalog_family, 'catalog_variant':'特殊档'}
        make_service(name='不同目录', **family)
        make_service(name='下架套餐', gameplay=service.gameplay, game_category=service.game_category, is_active=False, **family)
        result=self.client.get(f'/api/orders/services/{service.id}/').data['data']
        self.assertEqual(result['catalog_family'],'常规物资单')
        self.assertEqual(len(result['variants']),24)
        self.assertEqual({r['platform'] for r in result['variants']},{'PC','MOBILE'})
        self.assertEqual({r['security_level'] for r in result['variants']},{'CONFIDENTIAL','TOP_SECRET'})
        self.assertTrue(all(r['catalog_variant'] for r in result['variants']))
        bank=self.find('物资银行单')
        variants=self.client.get(f'/api/orders/services/{bank.id}/').data['data']['variants']
        self.assertEqual(len(variants),24)
        self.assertEqual(sum(r['is_addon'] for r in variants),4)
        self.assertTrue(all(r['catalog_family']=='物资银行单' for r in variants))

    def test_addons_do_not_supply_misleading_gameplay_starting_price(self):
        rows=self.client.get('/api/orders/gameplays/').data['data']
        bank=next(r for r in rows if r['code']=='xa-catalog-bank')
        self.assertEqual(bank['starting_price'],18800)
        self.assertEqual(bank['starting_price_unit'],'份')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '目录咨询/加购直购阻断（多人抢单校验先于客服咨询校验）属下单资格正式契约深水区（18-盘点 目录与下单资格待联调）；最终联调启用')
    def test_consultation_and_addons_cannot_be_purchased_directly(self):
        services=[self.find('物资银行加仓'), self.find('新客体验单'), self.find('常规陪玩',level='ANY')]
        for service in services:
            payload={'service_id':service.id,'game_rounds':1,'game_region':'国服','game_nickname':'测试','game_uid':'1234','client_request_id':'catalog-'+str(service.id)}
            for path in ['/api/orders/quote/','/api/orders/create/']:
                result=self.client.post(path,payload,format='json')
                self.assertEqual(result.data['code'],400,result.data)
                self.assertIn('客服',result.data['msg'])
        self.assertEqual(Order.objects.count(),0)
        self.assertEqual(Transaction.objects.count(),0)

    def test_normal_quote_and_admin_contract(self):
        service=self.find('常规物资单')
        payload={'service_id':service.id,'game_rounds':2,'game_region':'国服','game_nickname':'测试'}
        response=self.client.post('/api/orders/quote/',payload,format='json').data
        self.assertEqual(response['code'],0,response)
        self.assertEqual(response['data']['original_amount'],37600)
        data=AdminServiceItemSerializer(service).data
        self.assertEqual(data['price_unit'],'份')
        self.assertTrue(data['catalog_source'])
        consult=self.find('变废为宝',level='')
        serializer=AdminServiceItemSerializer(consult,data={'description':'展示价'},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors)

    def test_hiding_demo_is_scoped_and_preserves_references(self):
        regular=make_service(name='人工维护服务')
        demo=make_service(name='旧演示',description='本地测试方案 · 请设置服务内容与价格')
        call_command('seed_club_catalog',apply=True,hide_legacy_demos=True,stdout=io.StringIO())
        regular.refresh_from_db(); demo.refresh_from_db()
        self.assertTrue(regular.is_active)
        self.assertFalse(demo.is_active)
