from django.urls import path
from django.views.generic import RedirectView

from core import views
from core.api.views import api
from core.public_api.views import public_api

urlpatterns = [
    # pages
    path("", views.LandingView.as_view(), name="landing"),
    path("home", views.HomeView.as_view(), name="home"),
    path("admin-panel", views.AdminPanelView.as_view(), name="admin_panel"),
    path("settings", views.UserSettingsView.as_view(), name="settings"),
    path("privacy-policy", views.PrivacyPolicyView.as_view(), name="privacy_policy"),
    path("terms-of-service", views.TermsOfServiceView.as_view(), name="terms_of_service"),
    path("changelog", views.changelog_view, name="changelog"),
    # blog
    path("blog/", views.BlogView.as_view(), name="blog_posts"),
    path("blog/<slug:slug>", views.BlogPostView.as_view(), name="blog_post"),
    # app
    path("api/", api.urls),
    path("public-api/", public_api.urls),
    path(
        "project/<int:pk>/",
        RedirectView.as_view(pattern_name="project_home", permanent=False),
        name="project_redirect",
    ),
    path("project/<int:pk>/home/", views.ProjectHomeView.as_view(), name="project_home"),
    path(
        "project/<int:pk>/analytics/",
        views.ProjectAnalyticsView.as_view(),
        name="project_analytics",
    ),
    path(
        "project/<int:pk>/posts/eye-catching/",
        views.ProjectEyeCatchingPostsView.as_view(),
        name="project_eye_catching_posts",
    ),
    path(
        "project/<int:pk>/posts/seo-optimized/",
        views.ProjectSEOPostsView.as_view(),
        name="project_seo_posts",
    ),
    path(
        "project/<int:pk>/posts/custom/",
        views.ProjectCustomPostTypesView.as_view(),
        name="project_custom_post_types",
    ),
    path(
        "project/<int:pk>/posts/custom/<int:post_type_pk>/",
        views.ProjectCustomPostTypePostsView.as_view(),
        name="project_custom_post_type_posts",
    ),
    path(
        "project/<int:pk>/posts/custom/<int:post_type_pk>/edit/",
        views.ProjectCustomPostTypeEditView.as_view(),
        name="project_custom_post_type_edit",
    ),
    path(
        "project/<int:pk>/posts/custom/<int:post_type_pk>/update/",
        views.ProjectCustomPostTypeUpdateView.as_view(),
        name="project_custom_post_type_update",
    ),
    path(
        "project/<int:pk>/posts/custom/<int:post_type_pk>/delete/",
        views.ProjectCustomPostTypeDeleteView.as_view(),
        name="project_custom_post_type_delete",
    ),
    path(
        "project/<int:pk>/integrations/",
        views.ProjectIntegrationsView.as_view(),
        name="project_integrations",
    ),
    path(
        "project/integrations/google/callback/",
        views.ProjectIntegrationsGoogleCallbackView.as_view(),
        name="project_integrations_google_callback",
    ),
    path(
        "project/<int:pk>/settings/", views.ProjectSettingsView.as_view(), name="project_settings"
    ),
    path(
        "project/<int:pk>/keywords/", views.ProjectKeywordsView.as_view(), name="project_keywords"
    ),
    path("project/<int:pk>/pages/", views.ProjectPagesView.as_view(), name="project_pages"),
    path(
        "project/<int:project_pk>/pages/<int:page_pk>/",
        views.ProjectPageDetailView.as_view(),
        name="project_page_detail",
    ),
    path(
        "project/<int:pk>/earned-links/",
        views.ProjectEarnedLinksView.as_view(),
        name="project_earned_links",
    ),
    path(
        "project/<int:pk>/competitors/",
        views.ProjectCompetitorsView.as_view(),
        name="project_competitors",
    ),
    path(
        "project/<int:pk>/publish-history",
        views.PublishHistoryView.as_view(),
        name="publish_history",
    ),
    path("project/<int:pk>/delete/", views.ProjectDeleteView.as_view(), name="project_delete"),
    path(
        "project/<int:project_pk>/post/<int:pk>/",
        views.GeneratedBlogPostDetailView.as_view(),
        name="generated_blog_post_detail",
    ),
    path(
        "project/<int:project_pk>/post/<int:pk>/download-pdf/",
        views.download_blog_post_pdf,
        name="download_blog_post_pdf",
    ),
    path(
        "project/<int:project_pk>/competitor/<int:pk>/post/",
        views.CompetitorBlogPostDetailView.as_view(),
        name="competitor_blog_post_detail",
    ),
    # utils
    path("resend-confirmation/", views.resend_confirmation_email, name="resend_confirmation"),
    path("delete-account/", views.delete_account, name="delete_account"),
    # payments
    path("pricing", views.PricingView.as_view(), name="pricing"),
    path(
        "create-checkout-session/<str:product_name>/",
        views.create_checkout_session,
        name="user_upgrade_checkout_session",
    ),
    path(
        "subscription/upgrade/<str:product_name>/",
        views.upgrade_subscription,
        name="upgrade_subscription",
    ),
    path(
        "create-customer-portal/",
        views.create_customer_portal_session,
        name="create_customer_portal_session",
    ),
]
