# casts/apps.py

from django.apps import AppConfig

class CastsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'casts'

    def ready(self):
        # 导入 signals，使其生效
        import casts.signals