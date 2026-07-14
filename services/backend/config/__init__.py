"""加载 Celery 应用，使 Django 启动时自动注册 @shared_task。"""
from .celery import app as celery_app

__all__ = ['celery_app']
