from rest_framework import status
from rest_framework.response import Response

from .pagination import ConsolePagination
from .permissions import HasConsolePerm


class EnvelopeViewSetMixin:
    """统一后台 ViewSet 的鉴权、分页与 {code,data} 响应包装。

    子类可声明：
      - default_perm:    缺省权限点 code
      - required_perms:  {action: code 或 [code,...]} 精确控制各动作所需权限
    """

    permission_classes = [HasConsolePerm]
    pagination_class = ConsolePagination
    default_perm = None
    required_perms = {}

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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({'code': 0, 'data': serializer.data}, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({'code': 0, 'data': serializer.data})

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'code': 0, 'msg': 'deleted'})
