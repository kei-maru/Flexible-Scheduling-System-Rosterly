"""
Django settings for System B (SaaS API).
"""

from pathlib import Path
import os 

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-saas-key')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# =========================================================
# 1. 域名与主机配置
# =========================================================
ALLOWED_HOSTS = [
    '161.33.129.157',       # 你的公网 IP
    'vr-veludo.com',
    'saas.vr-veludo.com',
    
    # Docker 内部
    'system_b',             # docker-compose 服务名 (注意是下划线还是横线，代码里要做适配)
    'system-b',             # 兼容 host 名
    'veludo_system_b',      # 容器名
    
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
    
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',

    'tenants',
    'resources',
    'bookings',
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
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'static_root'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'tenants.SaaSUser'

SESSION_COOKIE_NAME = 'saas_sessionid'
CSRF_COOKIE_NAME = 'saas_csrftoken'

# Email (Gmail 配置)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'm17621752319@gmail.com'
EMAIL_HOST_PASSWORD = 'grsy mvtz uann nipw'
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER