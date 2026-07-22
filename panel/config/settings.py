from __future__ import annotations

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parent

env = environ.Env(
    DEBUG=(bool, True),
    ALLOWED_HOSTS=(list, ["127.0.0.1", "localhost"]),
    SECRET_KEY=(str, "dev-only-change-me-moneyprinter-panel"),
    DATABASE_URL=(str, f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
    YOUTUBE_CLIENT_SECRETS=(str, str(BASE_DIR / "credentials" / "youtube_client_secret.json")),
    YOUTUBE_OAUTH_REDIRECT_URI=(str, "http://127.0.0.1:8000/channels/oauth/callback/"),
    PANEL_DEFAULT_VOICE=(str, "pt-BR-FranciscaNeural-Female"),
    PANEL_DEFAULT_LANGUAGE=(str, "pt-BR"),
    PANEL_DEFAULT_ASPECT=(str, "9:16"),
    PANEL_DEFAULT_VIDEO_SOURCE=(str, "pexels"),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "panel.niches",
    "panel.channels",
    "panel.jobs",
    "panel.research",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "panel.config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "panel.config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MEDIA_URL = "/media/"
MEDIA_ROOT = REPO_ROOT / "storage" / "panel"

# MoneyPrinter engine defaults for niche factory jobs.
PANEL_REPO_ROOT = REPO_ROOT
PANEL_STORAGE_ROOT = REPO_ROOT / "storage" / "niches"
PANEL_DEFAULT_VOICE = env("PANEL_DEFAULT_VOICE")
PANEL_DEFAULT_LANGUAGE = env("PANEL_DEFAULT_LANGUAGE")
PANEL_DEFAULT_ASPECT = env("PANEL_DEFAULT_ASPECT")
PANEL_DEFAULT_VIDEO_SOURCE = env("PANEL_DEFAULT_VIDEO_SOURCE")

YOUTUBE_CLIENT_SECRETS = env("YOUTUBE_CLIENT_SECRETS")
YOUTUBE_OAUTH_REDIRECT_URI = env("YOUTUBE_OAUTH_REDIRECT_URI")
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY", default="")
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
