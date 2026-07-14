"""Celery 应用配置。

启动 worker:
    celery -A config worker -l info
启动 beat（定时任务，可选）:
    celery -A config beat -l info
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('xa')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
