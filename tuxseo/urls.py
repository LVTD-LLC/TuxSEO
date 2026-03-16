"""tuxseo URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from functools import partial

from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.generic import TemplateView
from django.views.generic.base import RedirectView
from ninja.openapi.views import openapi_json, openapi_view

from core.public_api.views import public_api
from core.views import (
    AccountSignupView,
    OnboardingFriendlyConfirmEmailView,
    skill_markdown_view,
    trigger_error,
)
from tuxseo.sitemaps import sitemaps

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/signup/", AccountSignupView.as_view(), name="account_signup"),
    re_path(
        r"^accounts/confirm-email/(?P<key>[-:\w]+)/$",
        OnboardingFriendlyConfirmEmailView.as_view(),
        name="account_confirm_email",
    ),
    path("accounts/", include("allauth.urls")),
    path("anymail/", include("anymail.urls")),
    path("uses", TemplateView.as_view(template_name="pages/uses.html"), name="uses"),
    path("skill.md", skill_markdown_view, name="skill_markdown"),
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
    path("api/docs", partial(openapi_view, api=public_api), name="api_docs"),
    path("api/docs/", partial(openapi_view, api=public_api), name="api_docs_slash"),
    path("api/openapi.json", partial(openapi_json, api=public_api), name="api_openapi_json"),
    path(
        "public-api/docs",
        RedirectView.as_view(url="/api/docs", permanent=False),
        name="legacy_public_api_docs",
    ),
    path(
        "public-api/docs/",
        RedirectView.as_view(url="/api/docs", permanent=False),
        name="legacy_public_api_docs_slash",
    ),
    path(
        "public-api/openapi.json",
        RedirectView.as_view(url="/api/openapi.json", permanent=False),
        name="legacy_public_api_openapi",
    ),
    path(
        "api/docs/<str:category>/<str:page>/",
        RedirectView.as_view(url="/docs/%(category)s/%(page)s/", permanent=False),
        name="legacy_api_docs_page",
    ),
    path(
        "docs",
        RedirectView.as_view(url="/docs/getting-started/introduction/", permanent=False),
    ),
    path(
        "docs/",
        RedirectView.as_view(url="/docs/getting-started/introduction/", permanent=False),
    ),
    path("docs/", include("docs.urls")),
    path("", include("core.urls")),
    path("", include("steering.urls")),
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("sentry-debug/", trigger_error),
]
