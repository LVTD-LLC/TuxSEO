from __future__ import annotations

from enum import StrEnum


class PlanEntitlement(StrEnum):
    PROJECT_CREATE = "PROJECT_CREATE"
    TITLE_GENERATION = "TITLE_GENERATION"
    CONTENT_GENERATION = "CONTENT_GENERATION"
    PRO_CONTENT_AUTOMATION = "PRO_CONTENT_AUTOMATION"
    PRO_OG_IMAGE_GENERATION = "PRO_OG_IMAGE_GENERATION"
    PRO_LINK_EXCHANGE = "PRO_LINK_EXCHANGE"
    COMPETITOR_ADD = "COMPETITOR_ADD"
    COMPETITOR_POST_GENERATION = "COMPETITOR_POST_GENERATION"
    KEYWORD_ADD = "KEYWORD_ADD"


def error_payload(code: str, message: str, *, upgrade_url: str | None = None) -> dict:
    payload = {
        "status": "error",
        "code": code,
        "message": message,
    }
    if upgrade_url:
        payload["upgrade_url"] = upgrade_url
    return payload


def ownership_not_found_payload(resource_label: str) -> dict:
    return error_payload(
        code="RESOURCE_NOT_FOUND",
        message=f"{resource_label} not found",
    )


def evaluate_plan_entitlement(
    profile,
    entitlement: PlanEntitlement,
    *,
    upgrade_url: str | None = None,
) -> dict | None:
    if entitlement == PlanEntitlement.PROJECT_CREATE:
        if getattr(profile, "can_create_project", True):
            return None
        if getattr(profile, "is_on_free_plan", False):
            limit = getattr(profile, "project_limit", 0)
            return error_payload(
                code="FREE_PLAN_PROJECT_LIMIT_REACHED",
                message=(
                    f"Project creation limit reached ({limit} project on Free plan). "
                    "Upgrade to Pro to create more projects."
                ),
                upgrade_url=upgrade_url,
            )
        return error_payload(
            code="PROJECT_LIMIT_REACHED",
            message="Project creation limit reached. Contact support for assistance.",
        )

    if entitlement == PlanEntitlement.TITLE_GENERATION:
        if getattr(profile, "can_generate_title_suggestions", True):
            return None
        limit = getattr(profile, "title_suggestion_limit", 0)
        current_count = getattr(profile, "number_of_title_suggestions_this_month", 0)
        return error_payload(
            code="FREE_PLAN_TITLE_SUGGESTION_LIMIT_REACHED",
            message=(
                f"Title generation limit reached ({current_count}/{limit} suggestions this month on Free plan). "
                "Upgrade to Pro for unlimited suggestions."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.CONTENT_GENERATION:
        if getattr(profile, "can_generate_blog_posts", True):
            return None
        limit = getattr(profile, "blog_post_generation_limit", 0)
        current_count = getattr(profile, "number_of_generated_blog_posts_this_month", 0)
        return error_payload(
            code="FREE_PLAN_BLOG_POST_LIMIT_REACHED",
            message=(
                f"Content generation limit reached ({current_count}/{limit} blog posts this month on Free plan). "
                "Upgrade to Pro for unlimited content."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.PRO_CONTENT_AUTOMATION:
        if profile.is_on_pro_plan:
            return None
        return error_payload(
            code="PRO_PLAN_REQUIRED_CONTENT_AUTOMATION",
            message=(
                "Automatic Post Submission is only available on the Pro plan. "
                "Please upgrade to access this feature."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.PRO_OG_IMAGE_GENERATION:
        if profile.is_on_pro_plan:
            return None
        return error_payload(
            code="PRO_PLAN_REQUIRED_OG_IMAGE_GENERATION",
            message=(
                "OG Image Generation is only available on the Pro plan. "
                "Please upgrade to access this feature."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.PRO_LINK_EXCHANGE:
        if profile.is_on_pro_plan:
            return None
        return error_payload(
            code="PRO_PLAN_REQUIRED_LINK_EXCHANGE",
            message=(
                "Link Exchange is only available on the Pro plan. "
                "Please upgrade to access this feature."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.COMPETITOR_ADD:
        if profile.can_add_competitors:
            return None
        return error_payload(
            code="PLAN_COMPETITOR_LIMIT_REACHED",
            message=(
                f"You have reached the competitor limit for your {profile.product_name} plan. "
                "Please upgrade to add more competitors."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.COMPETITOR_POST_GENERATION:
        if profile.can_generate_competitor_posts:
            return None
        return error_payload(
            code="PLAN_COMPETITOR_POST_LIMIT_REACHED",
            message=(
                f"You have reached the competitor post generation limit for your {profile.product_name} plan. "
                "Please upgrade to generate more competitor comparison posts."
            ),
            upgrade_url=upgrade_url,
        )

    if entitlement == PlanEntitlement.KEYWORD_ADD:
        if getattr(profile, "can_add_keywords", True):
            return None
        if getattr(profile, "is_on_free_plan", False):
            return error_payload(
                code="PRO_PLAN_REQUIRED_KEYWORD_ADDITION",
                message="Keyword additions are not available on the Free plan. Upgrade to Pro to add custom keywords.",
                upgrade_url=upgrade_url,
            )
        return error_payload(
            code="KEYWORD_LIMIT_REACHED",
            message="Keyword limit reached. Contact support for assistance.",
        )

    raise ValueError(f"Unsupported entitlement: {entitlement}")
