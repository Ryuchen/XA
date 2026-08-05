from rest_framework import mixins, status
from rest_framework.response import Response

from .pagination import ConsolePagination
from .permissions import HasConsolePerm


class EnvelopeViewSetMixin:
    """统一后台 ViewSet 的鉴权、分页与 {code,data} 响应包装。

    子类可声明：
      - default_perm:    缺省权限点 code
      - required_perms:  {action: code 或 [code,...]} 精确控制各动作所需权限

    注意写方法的「假路由」问题：本 mixin 排在 MRO 前面，因此即使子类继承的是
    ``ReadOnlyModelViewSet``，``create``/``update``/``destroy`` 依然存在于类上，
    DRF 的 SimpleRouter 靠 ``hasattr`` 决定要不要把 POST/PUT/DELETE 映射进 URL，
    于是只读 ViewSet 也会被挂上写路由，请求进来后再因为缺 ``perform_create``
    抛 AttributeError —— 对外表现是 500 而不是 405。

    所以每个写方法先确认子类真的混入了对应的 DRF 写 mixin，没有就老实返回 405。
    """

    permission_classes = [HasConsolePerm]
    pagination_class = ConsolePagination
    default_perm = None
    required_perms = {}

    @staticmethod
    def _method_not_allowed(request):
        return Response(
            {'code': 405, 'msg': f'不支持 {request.method} 方法'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response({'code': 0, 'data': {'list': serializer.data, 'total': len(serializer.data)}})

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({'code': 0, 'data': serializer.data})

    def create(self, request, *args, **kwargs):
        if not isinstance(self, mixins.CreateModelMixin):
            return self._method_not_allowed(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({'code': 0, 'data': serializer.data}, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not isinstance(self, mixins.UpdateModelMixin):
            return self._method_not_allowed(request)
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({'code': 0, 'data': serializer.data})

    def destroy(self, request, *args, **kwargs):
        if not isinstance(self, mixins.DestroyModelMixin):
            return self._method_not_allowed(request)
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'code': 0, 'msg': 'deleted'})
