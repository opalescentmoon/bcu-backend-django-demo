from .base import *  # noqa: F401,F403
from .base import BASE_DIR

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGE_BACKEND = "local"
MEDIA_ROOT = BASE_DIR / "tmp" / "test-media" / "public"
PRIVATE_MEDIA_ROOT = BASE_DIR / "tmp" / "test-media" / "private"
PAYMENT_PROVIDER = "simulation"
MIDTRANS_SERVER_KEY = "SB-Mid-server-TEST"
MIDTRANS_CLIENT_KEY = "SB-Mid-client-TEST"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "WARNING"}}
