# system_b_saas/config/celery.py

import os
from celery import Celery

# 设置 Django 的 settings 模块
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# 创建 Celery 实例
# 这里的名字 'config' 必须和 worker 启动命令里的 -A config 对应
app = Celery('config')

# 从 Django settings 加载配置，所有 CELERY_ 开头的配置都会被读入
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现任务 (tasks.py)
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')