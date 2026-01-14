from pathlib import Path
import os 

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-system-b-secret-key-change-in-production'

DEBUG = True

ALLOWED_HOSTS = []

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'rest_framework.authtoken',  # Token認証用
    'corsheaders',               # CORS対策

    # Local apps
    'tenants',
    'resources',
    'bookings',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # 必須: 一番上またはSecurityMiddlewareの後ろに配置
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

# Database
# System B用のデータベース設定 (PostgreSQL)
# 事前に: CREATE DATABASE saas_db; を実行してください
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'saas_db'),
        'USER': os.environ.get('DB_USER', 'dev_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'dev_password'),
        # 👇 关键：优先读取环境变量 DB_HOST，读不到才用 localhost
        # 这样在 Docker 里它会自动读到 'db'，在本地开发时读不到就会用 'localhost'
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

# REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# CORS Configuration
# System A (Veludo) からのアクセスを許可
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

LANGUAGE_CODE = 'ja' # 建议改为日语或英语
TIME_ZONE = 'Asia/Tokyo' # [关键] 强制东京时间

USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Custom User Model (SaaS用のユーザーモデル)
AUTH_USER_MODEL = 'tenants.SaaSUser'

SESSION_COOKIE_NAME = 'saas_sessionid'
CSRF_COOKIE_NAME = 'saas_csrftoken'

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'm17621752319@gmail.com' # 你的邮箱
EMAIL_HOST_PASSWORD = 'grsy mvtz uann nipw' # 谷歌应用专用密码 (不是邮箱登录密码)
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER