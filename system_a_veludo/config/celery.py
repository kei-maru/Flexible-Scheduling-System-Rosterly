import os
from celery import Celery

# 设置 Django 的默认 settings 模块路径
# 这里的 'config.settings' 对应你目录下的 config/settings.py
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# 创建 Celery 应用实例
app = Celery('config')

# 从 Django 的 settings.py 中读取配置
# 所有 Celery 相关的配置项在 settings.py 中必须以 "CELERY_" 开头
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现各个 app 目录下的 tasks.py 文件
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')