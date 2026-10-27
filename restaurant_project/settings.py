import os
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file into os.environ if present
env_file = BASE_DIR / ".env"
if env_file.exists():
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                k = key.strip()
                v = val.strip().strip("'\"")
                if k not in os.environ or not os.environ[k]:
                    os.environ[k] = v

import urllib.parse

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "replace-this-with-a-secure-secret-key-before-production")
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

allowed_hosts_raw = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,food-waves-rho.vercel.app,.vercel.app")
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_raw.split(",") if h.strip()]
for v_host in ["food-waves-rho.vercel.app", ".vercel.app", "127.0.0.1", "localhost"]:
    if v_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(v_host)

# Vercel reverse proxy HTTPS header
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Production security and HTTPS settings
if not DEBUG and "test" not in sys.argv:
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False").lower() in ("true", "1", "yes")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"

csrf_trusted_raw = os.getenv("CSRF_TRUSTED_ORIGINS", "https://food-waves-rho.vercel.app,https://*.vercel.app")
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_trusted_raw.split(",") if origin.strip()]
for v_origin in ["https://food-waves-rho.vercel.app", "https://*.vercel.app"]:
    if v_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(v_origin)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "restaurant",
]

try:
    import whitenoise  # noqa: F401
    HAS_WHITENOISE = True
except ImportError:
    HAS_WHITENOISE = False

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
]
if HAS_WHITENOISE:
    MIDDLEWARE.append("whitenoise.middleware.WhiteNoiseMiddleware")
MIDDLEWARE.extend([
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
])

ROOT_URLCONF = "restaurant_project.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "restaurant.context_processors.staff_role_context",
    ]},
}]

WSGI_APPLICATION = "restaurant_project.wsgi.application"

if "test" in sys.argv:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test_db.sqlite3",
        }
    }
else:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        parsed = urllib.parse.urlparse(db_url)
        db_name = parsed.path.lstrip("/").split("?")[0]
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": db_name or "postgres",
                "USER": urllib.parse.unquote(parsed.username or "postgres.smurbxgwcwsnzirrpqgh"),
                "PASSWORD": urllib.parse.unquote(parsed.password or "Sumanth@999"),
                "HOST": parsed.hostname or "aws-0-ap-south-1.pooler.supabase.com",
                "PORT": str(parsed.port or 5432),
                "CONN_MAX_AGE": 0,
            }
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": os.getenv("DB_NAME", "postgres"),
                "USER": os.getenv("DB_USER", "postgres.smurbxgwcwsnzirrpqgh"),
                "PASSWORD": os.getenv("DB_PASSWORD", "Sumanth@999"),
                "HOST": os.getenv("DB_HOST", "aws-0-ap-south-1.pooler.supabase.com"),
                "PORT": os.getenv("DB_PORT", "5432"),
                "CONN_MAX_AGE": 0,
            }
        }

    if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
        DATABASES["default"]["OPTIONS"] = {
            "sslmode": os.getenv("DB_SSLMODE", "require"),
            "connect_timeout": 10,
        }

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = [
    "restaurant.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend" if os.getenv("EMAIL_HOST_USER") else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    f"Food Waves <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "Food Waves <noreply@foodwaves.com>",
)

LOGIN_URL = "restaurant:login"
LOGIN_REDIRECT_URL = "restaurant:reservation"
LOGOUT_REDIRECT_URL = "restaurant:home"

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://smurbxgwcwsnzirrpqgh.supabase.co").rstrip("/")
SUPABASE_ANON_KEY = os.getenv(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNtdXJieGd3Y3dzbnppcnJwcWdoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTAxNjg1NTQsImV4cCI6MjEwNTc0NDU1NH0.WwvPZjEiWpTgxjLAyoATBlMiQ-_sJSIrNYV936uG2nk",
).strip()
