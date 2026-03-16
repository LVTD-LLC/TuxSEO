from pathlib import Path

import frontmatter
import markdown
import yaml
from django.conf import settings
from django.http import Http404
from django.shortcuts import render


def load_navigation_config():
    """
    Load navigation configuration from YAML file.
    Returns empty dict if file doesn't exist or has errors.
    """
    navigation_file = Path(settings.BASE_DIR) / "docs" / "navigation.yaml"

    if not navigation_file.exists():
        return {}

    try:
        with open(navigation_file, encoding="utf-8") as file:
            config = yaml.safe_load(file)
            return config.get("navigation", {}) if config else {}
    except Exception:
        return {}


def get_docs_navigation():  # noqa: C901
    """
    Build navigation structure from the docs/content directory.
    Uses custom ordering from navigation.yaml if defined, otherwise uses alphabetical order.
    Returns a list of dicts with category names and their pages.
    """
    content_dir = Path(settings.BASE_DIR) / "docs" / "content"
    navigation = []

    if not content_dir.exists():
        return navigation

    all_categories = {}
    for category_dir in content_dir.iterdir():
        if category_dir.is_dir():
            category_slug = category_dir.name
            all_categories[category_slug] = category_dir

    navigation_config = load_navigation_config()

    ordered_categories = []
    for category_slug in navigation_config.keys():
        if category_slug in all_categories:
            ordered_categories.append(category_slug)

    remaining_categories = sorted(set(all_categories.keys()) - set(ordered_categories))
    ordered_categories.extend(remaining_categories)

    for category_slug in ordered_categories:
        category_dir = all_categories[category_slug]
        category_name = category_slug.replace("-", " ").title()

        all_pages = {}
        for markdown_file in category_dir.glob("*.md"):
            page_slug = markdown_file.stem
            all_pages[page_slug] = markdown_file

        custom_page_order = navigation_config.get(category_slug, [])

        ordered_pages = []
        for page_slug in custom_page_order:
            if page_slug in all_pages:
                ordered_pages.append(page_slug)

        remaining_pages = sorted(set(all_pages.keys()) - set(ordered_pages))
        ordered_pages.extend(remaining_pages)

        pages = []
        for page_slug in ordered_pages:
            page_title = page_slug.replace("-", " ").title()
            pages.append(
                {
                    "slug": page_slug,
                    "title": page_title,
                    "url": f"/docs/{category_slug}/{page_slug}/",
                }
            )

        if pages:
            navigation.append(
                {
                    "category": category_name,
                    "category_slug": category_slug,
                    "pages": pages,
                }
            )

    return navigation


def get_flat_page_list(navigation):
    """
    Flatten the navigation structure into a single list of pages in order.
    Returns a list of dicts with category_slug, page_slug, page_title, and url.
    """
    flat_pages = []
    for category_item in navigation:
        for page_item in category_item["pages"]:
            flat_pages.append(
                {
                    "category_slug": category_item["category_slug"],
                    "page_slug": page_item["slug"],
                    "page_title": page_item["title"],
                    "url": page_item["url"],
                }
            )
    return flat_pages


def get_previous_and_next_pages(navigation, current_category, current_page):
    """
    Find the previous and next pages in the documentation navigation.
    Returns a tuple of (previous_page, next_page) where each is a dict or None.
    """
    flat_pages = get_flat_page_list(navigation)

    current_index = None
    for index, page_item in enumerate(flat_pages):
        if (
            page_item["category_slug"] == current_category
            and page_item["page_slug"] == current_page
        ):
            current_index = index
            break

    if current_index is None:
        return None, None

    previous_page = flat_pages[current_index - 1] if current_index > 0 else None
    next_page = flat_pages[current_index + 1] if current_index < len(flat_pages) - 1 else None

    return previous_page, next_page


def docs_page_view(request, category, page):
    """
    Render a documentation page from markdown file with frontmatter support.
    """
    content_dir = Path(settings.BASE_DIR) / "docs" / "content"
    markdown_file = content_dir / category / f"{page}.md"

    if not markdown_file.exists():
        raise Http404("Documentation page not found")

    try:
        with open(markdown_file, encoding="utf-8") as file:
            post = frontmatter.load(file)

        markdown_html = markdown.markdown(
            post.content, extensions=["fenced_code", "tables", "codehilite"]
        )

        navigation = get_docs_navigation()
        previous_page, next_page = get_previous_and_next_pages(navigation, category, page)

        default_page_title = page.replace("-", " ").title()
        default_category_title = category.replace("-", " ").title()

        context = {
            "content": markdown_html,
            "navigation": navigation,
            "current_category": category,
            "current_page": page,
            "page_title": post.get("title", default_page_title),
            "category_title": default_category_title,
            "meta_description": post.get("description", ""),
            "meta_keywords": post.get("keywords", ""),
            "author": post.get("author", ""),
            "canonical_url": post.get("canonical_url", ""),
            "previous_page": previous_page,
            "next_page": next_page,
        }

        return render(request, "docs/docs_page.html", context)
    except Exception as error:
        print(f"ERROR in docs_page_view: {error}")
        print(f"Category: {category}, Page: {page}")
        print(f"Markdown file: {markdown_file}")
        import traceback

        traceback.print_exc()
        raise
