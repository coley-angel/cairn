from django.http import HttpResponse
from django.urls import path


def healthz(_request):
    return HttpResponse("ok\n")


urlpatterns = [path("healthz", healthz)]
