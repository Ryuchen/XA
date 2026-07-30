from django.contrib.admin import AdminSite


class SuperuserOnlyAdminSite(AdminSite):
    site_header = '兴安电竞系统管理'
    site_title = '兴安电竞系统管理'
    index_title = '系统数据管理'

    def has_permission(self, request):
        return bool(
            request.user
            and request.user.is_active
            and request.user.is_superuser
        )
