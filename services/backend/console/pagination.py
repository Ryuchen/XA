from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class ConsolePagination(PageNumberPagination):
    """统一分页：?page=&page_size=，响应包成 {code,data:{list,total,page,page_size}}。"""

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            'code': 0,
            'data': {
                'list': data,
                'total': self.page.paginator.count,
                'page': self.page.number,
                'page_size': self.get_page_size(self.request),
            },
        })
