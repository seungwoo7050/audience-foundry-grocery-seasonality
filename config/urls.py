from django.contrib import admin
from django.urls import include, path

from grocery import health

urlpatterns = [
    path("health/", include((health.urlpatterns, health.app_name), namespace=health.app_name)),
    path("", include("grocery.urls")),
    path("admin/", admin.site.urls),
]
