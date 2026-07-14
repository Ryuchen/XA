"""C 端评价相关接口的单元测试。

覆盖：
- POST /api/orders/evaluate/          提交评价（校验各分支 + 评分聚合更新）
- GET  /api/orders/services/<id>/evaluations/  商品评价列表（均分/total/匿名脱敏）
- GET  /api/orders/evaluations/mine/  陪玩我收到的评价
- POST /api/orders/evaluations/<id>/reply/     陪玩回复（权限/空内容/成功）
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from orders.models import Evaluation, Order
from orders.tests.factories import (
    make_completed_order,
    make_evaluation,
    make_order,
    make_provider,
    make_service,
    make_user,
)


class EvaluateOrderTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000)
        self.order = make_completed_order(self.customer, self.provider, service=self.service)

    def test_evaluate_success_updates_rating(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {
            'order_id': self.order.id,
            'score': 4,
            'content': '服务很好',
        })
        self.assertEqual(res.data['code'], 0)
        self.assertTrue(Evaluation.objects.filter(order=self.order).exists())
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.rating_count, 1)
        self.assertEqual(self.provider.escort_profile.rating_avg, Decimal('4.00'))

    def test_evaluate_rejects_uncompleted_order(self):
        pending = make_order(self.customer, service=self.service, provider=self.provider)
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {'order_id': pending.id, 'score': 5})
        self.assertEqual(res.data['code'], 400)
        self.assertFalse(Evaluation.objects.filter(order=pending).exists())

    def test_evaluate_rejects_non_owner(self):
        other = make_user()
        self.client.force_authenticate(other)
        res = self.client.post('/api/orders/evaluate/', {'order_id': self.order.id, 'score': 5})
        self.assertEqual(res.data['code'], 400)

    def test_evaluate_rejects_duplicate(self):
        make_evaluation(self.order, score=3)
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {'order_id': self.order.id, 'score': 5})
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(Evaluation.objects.filter(order=self.order).count(), 1)

    def test_evaluate_rejects_invalid_score(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {'order_id': self.order.id, 'score': 6})
        self.assertEqual(res.data['code'], 400)

    def test_evaluate_requires_auth(self):
        res = self.client.post('/api/orders/evaluate/', {'order_id': self.order.id, 'score': 5})
        self.assertEqual(res.status_code, 401)


class DimensionEvaluationTest(APITestCase):
    """三维评价（技术/服务/沟通）提交与展示。"""

    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000)
        self.order = make_completed_order(self.customer, self.provider, service=self.service)

    def test_submit_three_dimensions(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {
            'order_id': self.order.id,
            'score': 4,
            'skill_score': 5,
            'attitude_score': 4,
            'communication_score': 3,
            'content': '技术好',
        })
        self.assertEqual(res.data['code'], 0)
        ev = Evaluation.objects.get(order=self.order)
        self.assertEqual(ev.skill_score, 5)
        self.assertEqual(ev.attitude_score, 4)
        self.assertEqual(ev.communication_score, 3)
        self.assertEqual(ev.avg_score, 4.0)
        self.assertEqual(res.data['data']['avg_score'], 4.0)

    def test_dimensions_default_to_overall_score(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {
            'order_id': self.order.id,
            'score': 3,
        })
        self.assertEqual(res.data['code'], 0)
        ev = Evaluation.objects.get(order=self.order)
        self.assertEqual(ev.skill_score, 3)
        self.assertEqual(ev.attitude_score, 3)
        self.assertEqual(ev.communication_score, 3)

    def test_rejects_invalid_dimension_score(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/evaluate/', {
            'order_id': self.order.id,
            'score': 4,
            'skill_score': 6,
        })
        self.assertEqual(res.data['code'], 400)
        self.assertFalse(Evaluation.objects.filter(order=self.order).exists())


class ServiceEvaluationListTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000)

    def _completed_order(self):
        return make_completed_order(self.customer, self.provider, service=self.service)

    def test_list_returns_avg_and_total(self):
        make_evaluation(self._completed_order(), score=5)
        make_evaluation(self._completed_order(), score=3)
        self.client.force_authenticate(self.customer)
        res = self.client.get(f'/api/orders/services/{self.service.id}/evaluations/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 2)
        self.assertEqual(res.data['data']['avg_score'], 4.0)
        self.assertEqual(len(res.data['data']['list']), 2)

    def test_anonymous_evaluation_is_masked(self):
        make_evaluation(self._completed_order(), score=5, is_anonymous=True)
        self.client.force_authenticate(self.customer)
        res = self.client.get(f'/api/orders/services/{self.service.id}/evaluations/')
        item = res.data['data']['list'][0]
        self.assertEqual(item['customer_name'], '匿名用户')
        self.assertEqual(item['customer_avatar'], '')

    def test_empty_service_returns_zero(self):
        self.client.force_authenticate(self.customer)
        res = self.client.get(f'/api/orders/services/{self.service.id}/evaluations/')
        self.assertEqual(res.data['data']['total'], 0)
        self.assertEqual(res.data['data']['avg_score'], 0)


class MyEvaluationListTest(APITestCase):
    def test_provider_sees_only_own_evaluations(self):
        customer = make_user()
        provider = make_provider()
        other_provider = make_provider()
        make_evaluation(make_completed_order(customer, provider), score=5)
        make_evaluation(make_completed_order(customer, other_provider), score=2)
        self.client.force_authenticate(provider)
        res = self.client.get('/api/orders/evaluations/mine/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['avg_score'], 5.0)


class ReplyEvaluationTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.order = make_completed_order(self.customer, self.provider)
        self.evaluation = make_evaluation(self.order, score=4)

    def test_provider_replies_success(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(
            f'/api/orders/evaluations/{self.evaluation.id}/reply/',
            {'reply_content': '感谢支持'},
        )
        self.assertEqual(res.data['code'], 0)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.reply_content, '感谢支持')
        self.assertIsNotNone(self.evaluation.replied_at)

    def test_reply_rejects_non_owner_provider(self):
        other_provider = make_provider()
        self.client.force_authenticate(other_provider)
        res = self.client.post(
            f'/api/orders/evaluations/{self.evaluation.id}/reply/',
            {'reply_content': '冒充回复'},
        )
        self.assertEqual(res.data['code'], 403)
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.reply_content, '')

    def test_reply_rejects_empty_content(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(
            f'/api/orders/evaluations/{self.evaluation.id}/reply/',
            {'reply_content': '   '},
        )
        self.assertEqual(res.data['code'], 400)

    def test_reply_missing_evaluation_returns_404(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(
            '/api/orders/evaluations/999999/reply/',
            {'reply_content': '回复'},
        )
        self.assertEqual(res.data['code'], 404)
