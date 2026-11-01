from django.contrib import admin
from .models import Room, Participant, Pool, Cell, Entry, EntryGrant, Draw, DrawBatch, Award, BonusAward

admin.site.site_header = '兴安电竞 · 独立抽奖系统'
admin.site.site_title = '店庆抽奖管理'
admin.site.index_title = '房间管理请进入 /control/；这里提供只读数据审计'
admin.site.site_url = '../control/'


class AuditAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [Room, Participant, Pool, Cell, Entry, EntryGrant, Draw, DrawBatch, Award, BonusAward]:
    admin.site.register(model, AuditAdmin)
