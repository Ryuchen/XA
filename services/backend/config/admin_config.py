from django.contrib.admin.apps import AdminConfig


class SuperuserOnlyAdminConfig(AdminConfig):
    default_site = 'config.admin_site.SuperuserOnlyAdminSite'
