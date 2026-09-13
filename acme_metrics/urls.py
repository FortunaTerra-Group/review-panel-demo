from django.urls import path

from metrics import views

urlpatterns = [
    path("healthz", views.healthz),
    path("v1/metrics/ingest", views.ingest),
    path("v1/metrics/summary", views.summary),
]
