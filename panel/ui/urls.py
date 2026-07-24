from __future__ import annotations

from django.urls import path

from panel.ui import views

app_name = "ui"

urlpatterns = [
    path("", views.home, name="home"),
    path("nichos/", views.niches_index, name="nichos"),
    path("nichos/descobrir/", views.niches_discover, name="niches_discover"),
    path("nichos/descoberta/<int:pk>/", views.niches_discovery, name="niches_discovery"),
    path(
        "nichos/descoberta/<int:pk>/adicionar/",
        views.niches_add_suggestion,
        name="niches_add_suggestion",
    ),
    path("nichos/<int:pk>/", views.niches_detail, name="niches_detail"),
    path(
        "nichos/<int:pk>/subnichos/",
        views.niches_discover_subs,
        name="niches_discover_subs",
    ),
    path("trends/", views.trends_index, name="trends_index"),
    path("trends/<int:pk>/", views.trends_detail, name="trends_detail"),
    path("trends/<int:pk>/usar/", views.trends_use_topic, name="trends_use_topic"),
    path("apis/", views.apis_index, name="apis_index"),
    path("apis/youtube/", views.apis_youtube_save, name="apis_youtube_save"),
    path("apis/youtube/testar/", views.apis_youtube_test, name="apis_youtube_test"),
    path("apis/nova/", views.apis_create, name="apis_create"),
    path("apis/testar/", views.apis_test_live, name="apis_test_live"),
    path("apis/<int:pk>/editar/", views.apis_edit, name="apis_edit"),
    path("apis/<int:pk>/testar/", views.apis_test, name="apis_test"),
    path("apis/<int:pk>/excluir/", views.apis_delete, name="apis_delete"),
    path("contas/", views.accounts_index, name="contas"),
    path("contas/nova/", views.accounts_create, name="accounts_create"),
    path("contas/<int:pk>/editar/", views.accounts_edit, name="accounts_edit"),
    path("contas/<int:pk>/excluir/", views.accounts_delete, name="accounts_delete"),
    path("roteiros/", views.scripts_index, name="scripts_index"),
    path("roteiros/gerar/", views.scripts_generate, name="scripts_generate"),
    path("roteiros/<int:pk>/", views.scripts_detail, name="scripts_detail"),
    path("roteiros/<int:pk>/regenerar/", views.scripts_regenerate, name="scripts_regenerate"),
    path("roteiros/<int:pk>/rescore/", views.scripts_rescore, name="scripts_rescore"),
    path("planos/", views.plans_index, name="plans_index"),
    path("planos/<int:pk>/", views.plans_detail, name="plans_detail"),
    path("planos/<int:pk>/salvar/", views.plans_save, name="plans_save"),
    path("planos/<int:pk>/regenerar/", views.plans_regenerate, name="plans_regenerate"),
    path("planos/<int:pk>/para-roteiro/", views.plans_to_script, name="plans_to_script"),
    path("cortes/", views.placeholder, {"area": "cortes"}, name="cortes"),
    path("create/", views.placeholder, {"area": "create"}, name="create"),
    path("publicar/", views.placeholder, {"area": "publicar"}, name="publicar"),
]
