from django.urls import path

from grocery import daily_views, history_views, selection_views, views

app_name = "grocery"

urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("selection/", selection_views.selection, name="selection"),
    path("series/<uuid:series_id>/", views.detail, name="detail"),
    path("series/<uuid:series_id>/history/", history_views.history, name="history"),
    path("series/<uuid:series_id>/regions/", daily_views.regions, name="regions"),
    path(
        "series/<uuid:series_id>/regions/<uuid:region_id>/markets/",
        daily_views.markets,
        name="markets",
    ),
    path("__qa__/catalog/<str:state>/", views.qa_catalog_state, name="qa_catalog_state"),
    path("__qa__/detail/<str:state>/", views.qa_detail_state, name="qa_detail_state"),
]
