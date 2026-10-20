import os

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "app"]
ROOT_URLCONF = "app.urls"
WSGI_APPLICATION = "app.wsgi.application"
