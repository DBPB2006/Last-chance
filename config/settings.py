import os
import sys
import tempfile
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / '.env')

# Core Security & Debug Configuration
DEBUG = os.getenv('DEBUG', 'True').strip().lower() in ('true', '1', 'yes')

SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-tsr-dev-secret-key-safe-local'
    else:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured("The SECRET_KEY environment variable must be set in production.")

# Host Configuration
env_allowed_hosts = os.getenv('ALLOWED_HOSTS', '').strip()
if env_allowed_hosts:
    ALLOWED_HOSTS = [host.strip() for host in env_allowed_hosts.split(',') if host.strip()]
    if DEBUG and 'testserver' not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append('testserver')
elif DEBUG:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver', '.ngrok-free.app', '.ngrok.io', '.loca.lt']
else:
    ALLOWED_HOSTS = []

# Trust reverse proxy X-Forwarded-Proto header for HTTPS detection (e.g. ngrok, local tunnels, production SSL)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

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

    # Project apps
    'accounts',
    'catalog',
    'orders',
    'ticketing',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'config.middleware.Custom404Middleware',
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

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 20,
        },
    }
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static & Media files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
if 'test' in sys.argv:
    MEDIA_ROOT = Path(tempfile.gettempdir()) / 'tsr_test_media'
else:
    MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'explore'
LOGOUT_REDIRECT_URL = 'login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework Settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
}

# CSRF Cookie settings (preserves Django forms and JavaScript/AJAX API requests)
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False  # Allows templates and API callers to read csrftoken for AJAX X-CSRFToken headers
SESSION_COOKIE_SAMESITE = 'Lax'

env_csrf_origins = os.getenv('CSRF_TRUSTED_ORIGINS', '').strip()
if env_csrf_origins:
    CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in env_csrf_origins.split(',') if origin.strip()]
elif DEBUG:
    CSRF_TRUSTED_ORIGINS = [
        'http://localhost:8000',
        'http://127.0.0.1:8000',
        'https://localhost:8000',
        'https://127.0.0.1:8000',
        'https://*.ngrok-free.app',
        'https://*.ngrok.io',
        'https://*.loca.lt',
    ]
else:
    CSRF_TRUSTED_ORIGINS = []

# Production Security Headers (active when DEBUG=False)
if not DEBUG:
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').strip().lower() in ('true', '1', 'yes')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

# Public Base URL for QR Ticket Credentials & Callbacks
TICKET_BASE_URL = os.getenv('TICKET_BASE_URL', '').strip().rstrip('/')
if not TICKET_BASE_URL and DEBUG:
    TICKET_BASE_URL = 'http://127.0.0.1:8000'

# Setu DigiLocker Sandbox Configuration
SETU_BASE_URL = os.getenv('SETU_BASE_URL', 'https://dg-sandbox.setu.co').strip()
SETU_CLIENT_ID = os.getenv('SETU_CLIENT_ID', '').strip()
SETU_CLIENT_SECRET = os.getenv('SETU_CLIENT_SECRET', '').strip()
SETU_PRODUCT_INSTANCE_ID = os.getenv('SETU_PRODUCT_INSTANCE_ID', '').strip()

SETU_REDIRECT_URL = os.getenv('SETU_REDIRECT_URL', '').strip()
if not SETU_REDIRECT_URL:
    if TICKET_BASE_URL:
        SETU_REDIRECT_URL = f"{TICKET_BASE_URL}/kyc/callback/"
    elif DEBUG:
        SETU_REDIRECT_URL = 'http://127.0.0.1:8000/kyc/callback/'

# Razorpay Payment Gateway Configuration
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID', '').strip()
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET', '').strip()

# Configure SQLite WAL mode to avoid database lock issues under concurrency
from django.db.backends.signals import connection_created
from django.dispatch import receiver

@receiver(connection_created)
def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode = WAL;')
            cursor.execute('PRAGMA synchronous = NORMAL;')
            cursor.execute('PRAGMA busy_timeout = 20000;')

