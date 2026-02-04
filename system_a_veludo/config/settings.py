"""
Django settings for System A (Veludo Main).
"""

from pathlib import Path
import os 

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-prod-key')

# 自动判断：如果在 .env 里写了 DEBUG=True，就是开发模式
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# =========================================================
# 1. 域名与主机配置 (同时兼容本地和线上)
# =========================================================
ALLOWED_HOSTS = [
    # --- 线上环境 ---
    '161.33.129.157',      # 你的公网 IP
    'vr-veludo.com',       # 你的域名 (预留)
    'www.vr-veludo.com',
    
    # --- Docker 内部 ---
    'system_a',            # Docker 服务名
    'veludo_system_a',     # Docker 容器名
    
    # --- 本地环境 ---
    'localhost',
    '127.0.0.1',
]

# =========================================================
# 2. CSRF 信任源 (Nginx 反向代理必须配置)
# =========================================================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = [
    'http://161.33.129.157',      # 允许通过 IP 访问提交表单
    'https://161.33.129.157',
    'http://vr-veludo.com',
    'https://vr-veludo.com',
    'http://localhost:8000',      # 本地开发
    'http://127.0.0.1:8000',
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    
    # Allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.discord',
    
    'rest_framework',
    'corsheaders',
    'core',
    'casts',
    'accounts',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database (适配 Docker)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'veludo_db'),
        'USER': os.environ.get('DB_USER', 'dev_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'dev_password'),
        'HOST': os.environ.get('DB_HOST', 'db'), # docker-compose 服务名
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': 600,
    }
}

LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_TZ = True

AUTH_USER_MODEL = 'core.User'

# 1. 静态文件 (System 图片)
STATIC_URL = '/static/'
STATIC_ROOT = '/app/static_root' # 指向 Docker 内部路径

# 告诉 Django 源文件在哪里 (你的图标就在这里面)
STATICFILES_DIRS = [
    BASE_DIR /  "static",
]

# 2. 媒体文件 (用户上传)
MEDIA_URL = '/media/'
MEDIA_ROOT = '/app/media'        # 指向 Docker 内部路径

# =========================================================
# SaaS 互联配置 (Docker 内部通讯)
# =========================================================
# 注意：这里使用的是 'system-b'，这是 docker-compose.yml 里的服务名
# 无论是在本地还是线上，只要在 Docker 里，这个名字都是通用的
SAAS_API_URL = 'http://system-b:8001/api/v1/integration'
SAAS_API_KEY = 'veludo_secret_key_123'
SAAS_API_KEY_HEADER = 'X-Tenant-Key'

SESSION_COOKIE_NAME = 'veludo_sessionid'
CSRF_COOKIE_NAME = 'veludo_csrftoken'

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Allauth Config
LOGIN_REDIRECT_URL = 'profile'
ACCOUNT_LOGOUT_REDIRECT_URL = 'index'
ACCOUNT_AUTHENTICATION_METHOD = 'username_email'
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_ADAPTER = 'core.adapters.MySocialAccountAdapter'

SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'SCOPE': ['identify', 'email'],
        'AUTH_PARAMS': {'prompt': 'none'},
        'VERIFIED_EMAIL': True,
    }
}

# =========================================================
# 3. Celery 异步任务配置 (必须添加！)
# =========================================================
# 告诉 Celery 去哪里找 Redis。
# 'veludo_redis' 是 docker-compose.yml 里的 container_name，Docker 内部会自动解析这个名字
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://veludo_redis:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://veludo_redis:6379/0')

# 时区设置，跟 Django 保持一致
CELERY_TIMEZONE = 'Asia/Tokyo'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# (可选) 防止任务死锁的超时设置
CELERY_TASK_SOFT_TIME_LIMIT = 300 
CELERY_TASK_TIME_LIMIT = 360