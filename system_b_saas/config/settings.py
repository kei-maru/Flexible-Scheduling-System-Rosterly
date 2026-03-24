"""
Django settings for System B (SaaS API).
"""

from pathlib import Path
import os 
import json

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-saas-key')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# =========================================================
# 1. 域名与主机配置
# =========================================================
ALLOWED_HOSTS = [
    '161.33.129.157',       # 你的公网 IP
    '138.3.221.225',
    'vr-veludo.com',
    'saas.vr-veludo.com',
    
    # Docker 内部
    'system_b',             # docker-compose 服务名
    'system-b',             # 兼容 host 名
    'veludo_system_b',      # 容器名
    'veludo-system-b',
    
    # 本地
    'localhost',
    '127.0.0.1',
]

# 代理设置 (Nginx 后面需要)
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.discord',
    
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',

    'tenants',
    'resources',
    'bookings',
    'dashboard',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware', # 必须放在最上面
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'saas_db'),
        'USER': os.environ.get('DB_USER', 'dev_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'dev_password'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': 600,
    }
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# =========================================================
# 2. CORS 配置 (允许谁调用我的 API)
# =========================================================
CORS_ALLOWED_ORIGINS = [
    # --- 本地开发 ---
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    
    # --- 线上环境 (IP访问) ---
    "http://161.33.129.157",       # 80端口访问 System A 的前端
    "http://161.33.129.157:8000",  # 直接访问 System A 端口
    
    # --- 线上环境 (域名访问) ---
    "http://vr-veludo.com",
    "https://vr-veludo.com",
]

LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'tenants.SaaSUser'
SITE_ID = int(os.environ.get('SITE_ID', '1'))

SESSION_COOKIE_NAME = 'saas_sessionid'
CSRF_COOKIE_NAME = 'saas_csrftoken'

# =========================================================
# 3. 静态文件与媒体文件 (核心修复)
# =========================================================
STATIC_URL = '/static/'
MEDIA_URL = '/media/'

# 👇 关键修改：使用绝对路径，指向 Docker 容器内的 /app/static_root
# 这样才能和 docker-compose.yml 里的 volumes 挂载点对齐
STATIC_ROOT = '/app/static_root'
MEDIA_ROOT = '/app/media'

# Email (Gmail 配置)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'keimarustudio@gmail.com'
EMAIL_HOST_PASSWORD = 'tmrh pdor cxjm zssu'
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER


# ====================================
# Auth & Redirect Settings
# ====================================

# 1. 登录成功后，自动跳转到哪里？ -> 公共主页面
LOGIN_REDIRECT_URL = 'shared_home'

# 2. 如果没登录就访问 dashboard，被踢到哪里？ -> 踢到 login
LOGIN_URL = 'dashboard_login'

# 3. 登出后去哪里？ -> 登录页
LOGOUT_REDIRECT_URL = 'dashboard_login'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

ACCOUNT_LOGIN_METHODS = {'username'}
ACCOUNT_SIGNUP_FIELDS = ['username*']
ACCOUNT_LOGIN_BY_CODE_ENABLED = False
ACCOUNT_LOGIN_BY_CODE_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = 'username'
ACCOUNT_EMAIL_REQUIRED = False
ACCOUNT_USERNAME_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = False
ACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_QUERY_EMAIL = False
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ADAPTER = 'tenants.adapters.SaaSDiscordSocialAdapter'

SYSTEM_B_DISCORD_CLIENT_ID = os.environ.get('SYSTEM_B_DISCORD_CLIENT_ID', '')
SYSTEM_B_DISCORD_SECRET = os.environ.get('SYSTEM_B_DISCORD_SECRET', '')
SYSTEM_B_DISCORD_KEY = os.environ.get('SYSTEM_B_DISCORD_KEY', '')
SYSTEM_B_DISCORD_AUTH_PROMPT = os.environ.get('SYSTEM_B_DISCORD_AUTH_PROMPT', 'consent')

SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'APP': {
            'client_id': SYSTEM_B_DISCORD_CLIENT_ID,
            'secret': SYSTEM_B_DISCORD_SECRET,
            'key': SYSTEM_B_DISCORD_KEY,
        },
        'SCOPE': ['identify'],
        'AUTH_PARAMS': {'prompt': SYSTEM_B_DISCORD_AUTH_PROMPT},
    }
}

SYSTEM_B_SSO_CODE_TTL_SECONDS = int(os.environ.get('SYSTEM_B_SSO_CODE_TTL_SECONDS', '60'))
SYSTEM_B_SSO_CLIENTS = {}

_sso_clients_raw = os.environ.get('SYSTEM_B_SSO_CLIENTS', '').strip()
if _sso_clients_raw:
    try:
        parsed = json.loads(_sso_clients_raw)
        if isinstance(parsed, dict):
            SYSTEM_B_SSO_CLIENTS = parsed
    except json.JSONDecodeError:
        SYSTEM_B_SSO_CLIENTS = {}

single_client_id = os.environ.get('SYSTEM_B_SSO_CLIENT_ID', '').strip()
single_client_secret = os.environ.get('SYSTEM_B_SSO_CLIENT_SECRET', '').strip()
single_redirect_uris = [
    uri.strip()
    for uri in os.environ.get('SYSTEM_B_SSO_REDIRECT_URIS', '').split(',')
    if uri.strip()
]
if single_client_id and single_client_secret and single_redirect_uris:
    SYSTEM_B_SSO_CLIENTS[single_client_id] = {
        'client_secret': single_client_secret,
        'redirect_uris': single_redirect_uris,
    }

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
