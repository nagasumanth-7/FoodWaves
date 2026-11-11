from django.contrib import admin
from django.urls import include, path, re_path
from restaurant.views import serve_stored_media

urlpatterns = [
    path("admin/", admin.site.urls),
    re_path(r"^media/(?P<path>.*)$", serve_stored_media, name="serve_media"),
    path("", include("restaurant.urls")),
]

handler403 = "restaurant.views.custom_permission_denied_view"

