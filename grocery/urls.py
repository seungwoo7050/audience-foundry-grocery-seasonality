from django.urls import path

from grocery import views

app_name = "grocery"

urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("series/<uuid:series_id>/", views.detail, name="detail"),
    path("__qa__/catalog/<str:state>/", views.qa_catalog_state, name="qa_catalog_state"),
    path("__qa__/detail/<str:state>/", views.qa_detail_state, name="qa_detail_state"),
]
