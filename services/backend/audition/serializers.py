from rest_framework import serializers

from .models import AuditionLink, AuditionSignup


class AuditionPublicSerializer(serializers.ModelSerializer):
    """免登录试音落地页的安全视图：只暴露展示字段，绝不回传任何 token。"""

    class Meta:
        model = AuditionLink
        fields = ['id', 'title', 'remark', 'expire_at', 'is_active']


class AuditionSignupCreateSerializer(serializers.ModelSerializer):
    """登录态下提交试音报名：仅接收报名人填写的字段。

    link 与 applicant 由视图根据 provider_token 与登录用户注入，
    不允许客户端直接指定，避免越权报名。
    """

    class Meta:
        model = AuditionSignup
        fields = ['contact', 'game', 'remark']


class MyAuditionSignupSerializer(serializers.ModelSerializer):
    """陪玩查看自己的报名记录：附带试音活动标题与状态中文。"""

    link_title = serializers.CharField(source='link.title', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = AuditionSignup
        fields = [
            'id', 'link', 'link_title', 'contact', 'game', 'remark',
            'status', 'status_display', 'audit_remark', 'audited_at', 'created_at',
        ]
