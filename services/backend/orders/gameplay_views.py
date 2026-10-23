from django.db.models import Count, Min, Q, OuterRef, Subquery, F, IntegerField, ExpressionWrapper
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from common.catalog_images import catalog_display_url
from .models import Gameplay, ServiceItem
from .catalog import visible_service_q
from .purchase_benefits import account_service_q


class GameplayListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        first_price = ServiceItem.objects.filter(visible_service_q(), account_service_q(getattr(request, 'account', None)), gameplay_id=OuterRef('pk'), is_addon=False).annotate(package_price=ExpressionWrapper(F('price') * F('billing_provider_count'), output_field=IntegerField())).order_by('package_price', 'id')
        rows = Gameplay.objects.filter(is_active=True).annotate(
            available_count=Count('services', filter=visible_service_q('services__') & account_service_q(getattr(request, 'account', None), 'services__')),
            starting_price=Min(F('services__price') * F('services__billing_provider_count'), filter=visible_service_q('services__') & account_service_q(getattr(request, 'account', None), 'services__') & Q(services__is_addon=False)),
            starting_price_unit=Subquery(first_price.values('price_unit')[:1]),
        )
        return Response({'code': 0, 'data': [{
            'id': row.id, 'name': row.name, 'code': row.code, 'kind': row.kind,
            'subtitle': row.subtitle, 'cover_url': catalog_display_url(row.cover_url), 'theme': row.theme,
            'rules': row.rules, 'service_count': row.available_count, 'starting_price': row.starting_price,
            'starting_price_unit': row.starting_price_unit or '份',
        } for row in rows]})
