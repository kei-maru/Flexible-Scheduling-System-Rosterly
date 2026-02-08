# system_b_saas/config/__init__.py

# 👇 这一步是为了确保 Django 启动时，Celery app 也被加载
from .celery import app as celery_app

__all__ = ('celery_app',)