import asyncio
import re
import secrets
import time
from urllib.parse import urlparse
from urllib.request import urlopen

import posthog
import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.forms.utils import ErrorList
from pydantic_ai import capture_run_messages

from core.choices import OGImageStyle
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

_AUTHORITY_MIN_SCORE = 0.15
_AUTHORITY_MIN_OVERLAP_RATIO = 0.12
LLM_ANALYTICS_EVENT = "$ai_generation"
_LLM_INPUT_PREVIEW_LIMIT = 500
_LLM_OUTPUT_PREVIEW_LIMIT = 1000


def _preview_text(value, limit=500):
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _safe_number(value):
    try:
        if value is None:
            return None
        number = float(value)
        if number < 0:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _first_non_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _extract_usage_metrics(result):
    usage = None

    usage_getter = getattr(result, "usage", None)
    if callable(usage_getter):
        usage = usage_getter()
    elif usage_getter is not None:
        usage = usage_getter

    if usage is None:
        return {}

    input_tokens = _safe_number(
        _first_non_none(
            getattr(usage, "input_tokens", None),
            getattr(usage, "request_tokens", None),
            getattr(usage, "prompt_tokens", None),
        )
    )
    output_tokens = _safe_number(
        _first_non_none(
            getattr(usage, "output_tokens", None),
            getattr(usage, "response_tokens", None),
            getattr(usage, "completion_tokens", None),
        )
    )
    total_tokens = _safe_number(getattr(usage, "total_tokens", None))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    metrics = {}
    if input_tokens is not None:
        metrics["$ai_input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        metrics["$ai_output_tokens"] = int(output_tokens)
    if total_tokens is not None:
        metrics["$ai_total_tokens"] = int(total_tokens)

    return metrics


def _resolve_distinct_id_from_deps(deps):
    if deps is None:
        return "tuxseo-agent"

    candidates = []
    candidates.append(getattr(deps, "distinct_id", None))

    user = getattr(deps, "user", None)
    if user is not None:
        candidates.append(getattr(user, "email", None))
        candidates.append(getattr(user, "id", None))

    profile = getattr(deps, "profile", None)
    if profile is not None:
        profile_user = getattr(profile, "user", None)
        candidates.append(getattr(profile_user, "email", None) if profile_user else None)
        candidates.append(getattr(profile_user, "id", None) if profile_user else None)
        candidates.append(getattr(profile, "id", None))

    candidates.append(getattr(deps, "user_id", None))
    candidates.append(getattr(deps, "profile_id", None))

    project = getattr(deps, "project", None)
    if project is not None:
        candidates.append(getattr(project, "id", None))

    candidates.append(getattr(deps, "id", None))

    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    return "tuxseo-agent"


def _resolve_agent_model_name(agent, fallback_model_name=""):
    model = getattr(agent, "model", None)
    if model is None:
        return fallback_model_name or "unknown"

    for attr in ("model_name", "model", "name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    return fallback_model_name or str(model)


def _emit_posthog_llm_generation(
    *,
    agent,
    input_string,
    result,
    deps,
    function_name,
    model_name,
    latency_seconds,
    error=None,
):
    if not settings.POSTHOG_API_KEY:
        return

    resolved_model = _resolve_agent_model_name(agent, fallback_model_name=model_name)
    feature_path = f"{model_name or 'unknown'}.{function_name or 'unknown'}"
    status = "failed" if error else "succeeded"

    properties = {
        "$ai_model": resolved_model,
        "$ai_input": [
            {
                "role": "user",
                "content": _preview_text(input_string, limit=_LLM_INPUT_PREVIEW_LIMIT),
            }
        ],
        "$ai_latency": round(max(latency_seconds, 0), 4),
        "feature_path": feature_path,
        "function_name": function_name,
        "model_name": model_name,
        "deps_type": deps.__class__.__name__ if deps is not None else None,
        "result_status": status,
    }

    if result is not None:
        properties["$ai_output_choices"] = [
            {
                "message": {
                    "content": _preview_text(
                        getattr(result, "output", ""),
                        limit=_LLM_OUTPUT_PREVIEW_LIMIT,
                    )
                }
            }
        ]
        properties.update(_extract_usage_metrics(result))

    if error is not None:
        properties["error_type"] = error.__class__.__name__
        properties["error_message"] = _preview_text(error, limit=300)

    cleaned_properties = {key: value for key, value in properties.items() if value is not None}

    try:
        posthog.capture(
            _resolve_distinct_id_from_deps(deps),
            event=LLM_ANALYTICS_EVENT,
            properties=cleaned_properties,
        )
    except Exception:  # noqa: BLE001 - telemetry should never interrupt generation flows
        logger.warning(
            "[Run Agent Synchronously] Failed to emit PostHog LLM analytics event",
            exc_info=True,
            function_name=function_name,
            model_name=model_name,
        )


class DivErrorList(ErrorList):
    def __str__(self):
        return self.as_divs()

    def as_divs(self):
        if not self:
            return ""
        return f"""
            <div class="p-4 my-4 bg-red-50 rounded-md border border-red-600 border-solid">
              <div class="flex">
                <div class="flex-shrink-0">
                  <!-- Heroicon name: solid/x-circle -->
                  <svg class="w-5 h-5 text-red-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" />
                  </svg>
                </div>
                <div class="ml-3 text-sm text-red-700">
                      {"".join([f"<p>{e}</p>" for e in self])}
                </div>
              </div>
            </div>
         """  # noqa: E501


def replace_placeholders(data, blog_post):
    """
    Recursively replace values in curly braces (e.g., '{{ slug }}')
    in a dict with the corresponding attribute from blog_post.
    """
    if isinstance(data, dict):
        return {k: replace_placeholders(v, blog_post) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_placeholders(item, blog_post) for item in data]
    elif isinstance(data, str):
        import re

        def repl(match):
            attr = match.group(1).strip()
            # Support nested attributes (e.g., title.title)
            value = blog_post
            for part in attr.split("."):
                value = getattr(value, part, match.group(0))
                if value == match.group(0):
                    break
            return str(value)

        return re.sub(r"\{\{\s*(.*?)\s*\}\}", repl, data)
    else:
        return data


def get_og_image_prompt(style: str, category: str) -> str:
    """
    Generate a style-specific prompt for OG image generation.

    Args:
        style: The OG image style from OGImageStyle choices
        category: The blog post category

    Returns:
        A prompt string optimized for the selected style
    """
    base_format = "1200x630 pixels aspect ratio. NO TEXT, NO WORDS, NO LETTERS."

    style_prompts = {
        OGImageStyle.MODERN_GRADIENT: f"Modern gradient background for a social media post about {category}. Contemporary smooth color transitions, flowing shapes, clean composition. {base_format}",  # noqa: E501
        OGImageStyle.MINIMALIST_CLEAN: f"Minimalist clean background for a social media post about {category}. Simple geometric shapes, plenty of white space, subtle colors, elegant and professional. {base_format}",  # noqa: E501
        OGImageStyle.BOLD_TYPOGRAPHY: f"Bold graphic background for a social media post about {category}. Strong geometric shapes, high contrast, eye-catching composition, modern and dynamic. {base_format}",  # noqa: E501
        OGImageStyle.TECH_ABSTRACT: f"Tech abstract background for a social media post about {category}. Geometric patterns, grid lines, digital aesthetic, futuristic feel, technology-inspired visuals. {base_format}",  # noqa: E501
        OGImageStyle.PROFESSIONAL_CORPORATE: f"Professional corporate background for a social media post about {category}. Polished appearance, business-friendly colors, clean lines, sophisticated composition. {base_format}",  # noqa: E501
        OGImageStyle.CREATIVE_ARTISTIC: f"Creative artistic background for a social media post about {category}. Unique visual elements, artistic flair, expressive composition, vibrant and imaginative. {base_format}",  # noqa: E501
        OGImageStyle.DARK_MODE: f"Dark mode background for a social media post about {category}. Dark background with vibrant accent colors, modern contrast, sleek and contemporary aesthetic. {base_format}",  # noqa: E501
        OGImageStyle.VIBRANT_COLORFUL: f"Vibrant colorful background for a social media post about {category}. Bold colors, energetic composition, dynamic visual elements, eye-catching and lively. {base_format}",  # noqa: E501
    }

    return style_prompts.get(
        style,
        f"Abstract modern geometric background for a social media post about {category}. Clean minimalist design with vibrant gradients, smooth shapes, professional aesthetic. {base_format}",  # noqa: E501
    )


def download_image_from_url(
    image_url: str, field_name: str, instance_id: str | int
) -> ContentFile | None:
    """
    Download an image from a URL and return a ContentFile ready to be saved to an ImageField.

    Args:
        image_url: The URL of the image to download
        field_name: The name of the field (e.g., 'icon', 'image') for logging and filename
        instance_id: The ID of the model instance for logging and filename

    Returns:
        ContentFile containing the image data, or None if download fails
    """
    try:
        logger.info(
            f"[DownloadImage] Downloading {field_name} from URL",
            image_url=image_url,
            field_name=field_name,
            instance_id=instance_id,
        )

        image_response = urlopen(image_url)
        image_content = ContentFile(image_response.read())

        logger.info(
            f"[DownloadImage] Successfully downloaded {field_name}",
            image_url=image_url,
            field_name=field_name,
            instance_id=instance_id,
        )

        return image_content

    except Exception as error:
        logger.error(
            f"[DownloadImage] Failed to download {field_name} from URL",
            error=str(error),
            exc_info=True,
            image_url=image_url,
            field_name=field_name,
            instance_id=instance_id,
        )
        return None


def get_jina_embedding(text: str) -> list[float] | None:
    """
    Get embedding from Jina API for the given text.

    Args:
        text: The text to generate an embedding for

    Returns:
        A list of floats representing the embedding vector, or None if the request fails
    """
    url = "https://api.jina.ai/v1/embeddings"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.JINA_READER_API_KEY}",
    }
    data = {"model": "jina-embeddings-v3", "task": "text-matching", "input": [text]}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
        result = response.json()

        if result.get("data") and len(result["data"]) > 0:
            embedding = result["data"][0]["embedding"]
            logger.info(
                "[GetJinaEmbedding] Successfully generated embedding",
                embedding_dimensions=len(embedding),
            )
            return embedding
        else:
            logger.error(
                "[GetJinaEmbedding] Unexpected response format from Jina API",
                result=result,
            )
            return None

    except requests.exceptions.RequestException as request_error:
        logger.error(
            "[GetJinaEmbedding] Error getting embedding from Jina API",
            error=str(request_error),
            exc_info=True,
        )
        return None


def generate_random_key():
    """Generate a high-entropy API key with an explicit product prefix.

    Format: ``tuxseo_<40 lowercase hex chars>`` (160 bits entropy)
    Example: ``tuxseo_a3f5...``
    """
    return f"tuxseo_{secrets.token_hex(20)}"


def get_html_content(url):
    html_content = ""
    try:
        html_response = requests.get(url, timeout=30)
        html_response.raise_for_status()
        html_content = html_response.text
    except requests.exceptions.RequestException as e:
        logger.warning(
            "[Get HTML Content] Could not fetch HTML content",
            exc_info=True,
            error=str(e),
            url=url,
        )
    except Exception as e:
        logger.warning(
            "[Get HTML Content] Unexpected error",
            exc_info=True,
            error=str(e),
            url=url,
        )

    return html_content


def get_markdown_content(url):
    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.JINA_READER_API_KEY}",
    }

    try:
        response = requests.get(jina_url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json().get("data", {})

        logger.info(
            "[Get Markdown Content] Successfully fetched content from Jina Reader",
            data=data,
            url=url,
        )

        return (
            data.get("title", "")[:500],
            data.get("description", ""),
            data.get("content", ""),
        )

    except requests.exceptions.RequestException as e:
        logger.error(
            "[Get Markdown Content] Error fetching content from Jina Reader",
            error=str(e),
            exc_info=True,
            url=url,
        )
        return ("", "", "")


def run_agent_synchronously(agent, input_string, deps=None, function_name="", model_name=""):
    """
    Run a PydanticAI agent synchronously.

    Args:
        agent: The PydanticAI agent to run
        input_string: The input string to pass to the agent
        deps: Optional dependencies to pass to the agent

    Returns:
        The result of the agent run

    Raises:
        RuntimeError: If the agent execution fails
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    with capture_run_messages() as messages:
        started_at = time.perf_counter()
        try:
            logger.info(
                "[Run Agent Synchronously] Running agent",
                messages=messages,
                input_string=input_string,
                deps=deps,
                function_name=function_name,
                model_name=model_name,
            )
            if deps is not None:
                result = loop.run_until_complete(agent.run(input_string, deps=deps))
            else:
                result = loop.run_until_complete(agent.run(input_string))

            elapsed = time.perf_counter() - started_at
            _emit_posthog_llm_generation(
                agent=agent,
                input_string=input_string,
                result=result,
                deps=deps,
                function_name=function_name,
                model_name=model_name,
                latency_seconds=elapsed,
            )

            logger.info(
                "[Run Agent Synchronously] Agent run successfully",
                messages=messages,
                input_string=input_string,
                deps=deps,
                result=result,
                latency_seconds=elapsed,
                function_name=function_name,
                model_name=model_name,
            )
            return result
        except Exception as e:
            elapsed = time.perf_counter() - started_at
            _emit_posthog_llm_generation(
                agent=agent,
                input_string=input_string,
                result=None,
                deps=deps,
                function_name=function_name,
                model_name=model_name,
                latency_seconds=elapsed,
                error=e,
            )
            logger.error(
                "[Run Agent Synchronously] Failed execution",
                messages=messages,
                exc_info=True,
                error=str(e),
                latency_seconds=elapsed,
                function_name=function_name,
                model_name=model_name,
            )
            raise


def run_gptr_synchronously(agent, custom_prompt=None):
    """
    Run a GPTR agent synchronously.

    Args:
        agent: The GPTR agent to run
        custom_prompt: Optional custom prompt to pass to the agent

    Returns:
        The result of the agent run

    Raises:
        RuntimeError: If the agent execution fails
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        logger.info(
            "[Run GPTR Synchronously] Running agent",
            agent=agent,
            custom_prompt=custom_prompt,
            has_custom_prompt=custom_prompt is not None,
        )

        if custom_prompt is None:
            result = loop.run_until_complete(agent.write_report())
        else:
            result = loop.run_until_complete(agent.write_report(custom_prompt=custom_prompt))

        logger.info(
            "[Run GPTR Synchronously] Agent run successfully",
            custom_prompt=custom_prompt,
            has_custom_prompt=custom_prompt is not None,
            result_length=len(str(result)) if result else 0,
        )
        return result

    except Exception as error:
        logger.error(
            "[Run GPTR Synchronously] Failed execution",
            exc_info=True,
            error=str(error),
            custom_prompt=custom_prompt,
            has_custom_prompt=custom_prompt is not None,
        )
        raise


def extract_title_from_content(content: str) -> tuple[str | None, str]:
    """
    Extract the title from blog post content and remove it from the content.

    The function looks for a markdown H1 title (# Title) at the beginning of the content,
    extracts it, and returns both the title and the content with the title removed.

    Args:
        content: The full blog post content as markdown text

    Returns:
        A tuple of (title, content_without_title) where:
        - title is the extracted title string or None if no title found
        - content_without_title is the remaining content after title removal

    Example:
        >>> content = "# My Blog Title\\n\\nSome content here"
        >>> title, content = extract_title_from_content(content)
        >>> print(title)
        "My Blog Title"
        >>> print(content)
        "Some content here"
    """
    import re

    if not content or not content.strip():
        logger.warning("[ExtractTitleFromContent] Empty or whitespace-only content provided")
        return None, content

    lines = content.strip().split("\n")

    if not lines:
        logger.warning("[ExtractTitleFromContent] No lines found in content")
        return None, content

    first_line = lines[0].strip()

    title_match = re.match(r"^#\s+(.+)$", first_line)

    if not title_match:
        logger.warning(
            "[ExtractTitleFromContent] No H1 title found at the beginning of content",
            first_line_preview=first_line[:100],
        )
        return None, content

    extracted_title = title_match.group(1).strip()

    remaining_lines = lines[1:]
    content_without_title = "\n".join(remaining_lines).strip()

    logger.info(
        "[ExtractTitleFromContent] Successfully extracted title from content",
        extracted_title=extracted_title,
        original_content_length=len(content),
        new_content_length=len(content_without_title),
    )

    return extracted_title, content_without_title


def process_generated_blog_content(
    generated_content: str, fallback_title: str, title_suggestion_id: int, project_id: int
) -> tuple[str, str]:
    """
    Process generated blog content by extracting title and cleaning up unwanted sections.

    This function:
    1. Extracts the H1 title from the content
    2. Removes the title from the content
    3. Removes the "## Introduction" header if present
    4. Falls back to provided title if extraction fails

    Args:
        generated_content: The raw generated blog post content
        fallback_title: Title to use if extraction fails
        title_suggestion_id: ID of the title suggestion for logging
        project_id: ID of the project for logging

    Returns:
        A tuple of (blog_post_title, blog_post_content) where:
        - blog_post_title is the extracted title or fallback title
        - blog_post_content is the cleaned content without title and introduction header

    Example:
        >>> content = "# My Title\\n\\n## Introduction\\n\\nSome intro text\\n\\nMore content"
        >>> title, content = process_generated_blog_content(content, "Fallback", 1, 1)
        >>> print(title)
        "My Title"
        >>> print(content)
        "Some intro text\\n\\nMore content"
    """
    import re

    extracted_title, content_without_title = extract_title_from_content(generated_content)

    if extracted_title:
        blog_post_title = extracted_title
        blog_post_content = content_without_title
        logger.info(
            "[ProcessGeneratedBlogContent] Successfully extracted title from generated content",
            title_suggestion_id=title_suggestion_id,
            project_id=project_id,
            extracted_title=extracted_title,
        )
    else:
        blog_post_title = fallback_title
        blog_post_content = generated_content
        logger.error(
            "[ProcessGeneratedBlogContent] Failed to extract title from content, using fallback title",  # noqa: E501
            title_suggestion_id=title_suggestion_id,
            project_id=project_id,
            fallback_title=fallback_title,
        )

    introduction_pattern = r"^##\s+Introduction\s*\n+"
    content_cleaned = re.sub(
        introduction_pattern, "", blog_post_content, count=1, flags=re.MULTILINE
    )

    if content_cleaned != blog_post_content:
        logger.info(
            "[ProcessGeneratedBlogContent] Removed Introduction header from content",
            title_suggestion_id=title_suggestion_id,
            project_id=project_id,
        )
        blog_post_content = content_cleaned

    references_pattern = r"\n---\n+##\s+References\s*\n.*?(?=\n---\n|$)"
    content_without_references = re.sub(references_pattern, "", blog_post_content, flags=re.DOTALL)

    if content_without_references != blog_post_content:
        logger.info(
            "[ProcessGeneratedBlogContent] Removed References section from content",
            title_suggestion_id=title_suggestion_id,
            project_id=project_id,
        )
        blog_post_content = content_without_references

    horizontal_rule_pattern = r"\n---\n"
    content_without_horizontal_rules = re.sub(horizontal_rule_pattern, "\n\n", blog_post_content)

    if content_without_horizontal_rules != blog_post_content:
        logger.info(
            "[ProcessGeneratedBlogContent] Removed horizontal rule patterns from content",
            title_suggestion_id=title_suggestion_id,
            project_id=project_id,
        )
        blog_post_content = content_without_horizontal_rules

    return blog_post_title, blog_post_content


def get_relevant_pages_for_blog_post(project, meta_description: str, max_pages: int = 5):
    """
    Find the most relevant project pages for a blog post based on semantic similarity.

    This function converts the meta description into an embedding vector and finds
    project pages with similar content using vector similarity search with pgvector.

    Args:
        project: The Project instance to search pages within
        meta_description: The meta description text to find relevant pages for
        max_pages: Maximum number of relevant pages to return (default: 5)

    Returns:
        QuerySet of ProjectPage objects ordered by relevance (most relevant first),
        or empty queryset if embedding generation fails or no pages have embeddings

    Example:
        # Get relevant pages for a title suggestion's meta description
        title_suggestion = BlogPostTitleSuggestion.objects.get(id=123)
        relevant_pages = get_relevant_pages_for_blog_post(
            project=title_suggestion.project,
            meta_description=title_suggestion.suggested_meta_description,
            max_pages=10
        )

        # Use the relevant pages in blog post generation
        for page in relevant_pages:
            print(f"Relevant page: {page.title} - {page.url}")
    """
    from pgvector.django import CosineDistance

    from core.models import ProjectPage

    if not meta_description or not meta_description.strip():
        logger.warning(
            "[GetRelevantPages] Empty meta description provided",
            project_id=project.id,
            project_name=project.name,
        )
        return ProjectPage.objects.none()

    meta_description_embedding = get_jina_embedding(meta_description)

    if not meta_description_embedding:
        logger.error(
            "[GetRelevantPages] Failed to generate embedding for meta description",
            project_id=project.id,
            project_name=project.name,
            meta_description_length=len(meta_description),
        )
        return ProjectPage.objects.none()

    pages_with_embeddings = project.project_pages.filter(
        embedding__isnull=False,
        date_analyzed__isnull=False,
    )

    if not pages_with_embeddings.exists():
        logger.info(
            "[GetRelevantPages] No pages with embeddings found for project",
            project_id=project.id,
            project_name=project.name,
        )
        return ProjectPage.objects.none()

    relevant_pages = pages_with_embeddings.order_by(
        CosineDistance("embedding", meta_description_embedding)
    )[:max_pages]

    logger.info(
        "[GetRelevantPages] Successfully found relevant pages",
        project_id=project.id,
        project_name=project.name,
        num_relevant_pages=len(relevant_pages),
        max_pages=max_pages,
        total_pages_with_embeddings=pages_with_embeddings.count(),
        meta_description_preview=meta_description[:100],
    )

    return relevant_pages


def _is_likely_authority_domain(domain: str) -> bool:
    domain = (domain or "").lower()
    if not domain:
        return False

    blocked_suffixes = (
        "reddit.com",
        "quora.com",
        "pinterest.com",
        "medium.com",
        "substack.com",
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "x.com",
        "twitter.com",
    )

    return not any(domain == suffix or domain.endswith(f".{suffix}") for suffix in blocked_suffixes)


def _tokenize_relevance_text(value: str) -> set[str]:
    stopwords = {
        "about",
        "after",
        "also",
        "because",
        "from",
        "have",
        "into",
        "more",
        "that",
        "their",
        "them",
        "they",
        "this",
        "what",
        "when",
        "where",
        "with",
        "your",
    }
    tokens = re.findall(r"[a-z0-9]{4,}", (value or "").lower())
    return {token for token in tokens if token not in stopwords}


def _passes_authority_relevance_gate(*, query: str, title: str, text_snippet: str, score) -> bool:
    query_tokens = _tokenize_relevance_text(query)
    if not query_tokens:
        return False

    candidate_tokens = _tokenize_relevance_text(f"{title} {text_snippet}")
    overlap_ratio = len(query_tokens.intersection(candidate_tokens)) / max(len(query_tokens), 1)

    parsed_score = None
    if score is not None:
        try:
            parsed_score = float(score)
        except (TypeError, ValueError):
            parsed_score = None

    if parsed_score is not None and parsed_score < _AUTHORITY_MIN_SCORE:
        return False

    return overlap_ratio >= _AUTHORITY_MIN_OVERLAP_RATIO


def get_external_authority_link_candidates(meta_description: str, max_links: int = 2):
    """Fetch relevance-gated external authority links for generation planning.

    `max_links` is defensively clamped here as this helper may be called outside
    generation context with unsanitized input.
    """
    max_links = max(1, min(3, int(max_links or 1)))

    if not meta_description or not meta_description.strip():
        return []

    exa_api_key = (getattr(settings, "EXA_API_KEY", "") or "").strip()
    if not exa_api_key:
        return []

    try:
        response = requests.post(
            "https://api.exa.ai/search",
            headers={
                "x-api-key": exa_api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": meta_description,
                "type": "auto",
                "num_results": max(8, max_links * 4),
                "contents": {
                    "highlights": {
                        "numSentences": 2,
                    }
                },
            },
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.warning(
            "[ExternalAuthorityLinks] Exa lookup failed",
            error=str(error),
            exc_info=True,
            max_links=max_links,
        )
        return []

    results = response.json().get("results", [])
    selected_links = []
    seen_urls = set()

    for item in results:
        url = (item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue

        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"}:
            continue

        domain = (parsed_url.hostname or "").lower()
        if not _is_likely_authority_domain(domain):
            continue

        title = (item.get("title") or "").strip()
        highlights = item.get("highlights") or []
        text_snippet = " ".join(highlights) if isinstance(highlights, list) else str(highlights)
        if not _passes_authority_relevance_gate(
            query=meta_description,
            title=title,
            text_snippet=text_snippet,
            score=item.get("score"),
        ):
            continue

        selected_links.append(
            {
                "url": url,
                "title": title or domain,
                "description": text_snippet[:280],
                "summary": text_snippet[:400],
                "link_source": "external",
            }
        )
        seen_urls.add(url)

        if len(selected_links) >= max_links:
            break

    return selected_links


def get_relevant_external_pages_for_blog_post(
    meta_description: str, exclude_project=None, max_pages: int = 3
):
    """
    Find the most relevant pages from other link-exchange projects for a blog post.

    This function searches across project pages with embeddings,
    finds those from projects participating in link exchange, and returns
    the most relevant ones based on semantic similarity to the blog post's meta description.

    Args:
        meta_description: The meta description text to find relevant pages for
        exclude_project: Project instance to exclude pages from (typically the project we're writing for)
        max_pages: Maximum number of relevant pages to return (default: 3)

    Returns:
        QuerySet of ProjectPage objects ordered by relevance (most relevant first),
        or empty queryset if embedding generation fails or no pages have embeddings

    Example:
        # Get relevant external pages for a title suggestion's meta description
        title_suggestion = BlogPostTitleSuggestion.objects.get(id=123)
        relevant_external_pages = get_relevant_external_pages_for_blog_post(
            meta_description=title_suggestion.suggested_meta_description,
            exclude_project=title_suggestion.project,
            max_pages=5
        )

        # Use the relevant external pages in blog post generation
        for page in relevant_external_pages:
            print(f"Relevant external page: {page.title} - {page.url}")
    """  # noqa: E501
    from pgvector.django import CosineDistance

    from core.models import ProjectPage

    if not meta_description or not meta_description.strip():
        logger.warning("[GetRelevantExternalPages] Empty meta description provided")
        return ProjectPage.objects.none()

    meta_description_embedding = get_jina_embedding(meta_description)

    if not meta_description_embedding:
        logger.error(
            "[GetRelevantExternalPages] Failed to generate embedding for meta description",
            meta_description_length=len(meta_description),
        )
        return ProjectPage.objects.none()

    eligible_external_pages_query = ProjectPage.objects.filter(
        embedding__isnull=False,
        date_analyzed__isnull=False,
        project__profile__isnull=False,
        project__particiate_in_link_exchange=True,
    )

    if exclude_project:
        eligible_external_pages_query = eligible_external_pages_query.exclude(project=exclude_project)

    eligible_external_pages = eligible_external_pages_query.select_related("project__profile")

    relevant_external_pages = list(
        eligible_external_pages.order_by(CosineDistance("embedding", meta_description_embedding))[
            :max_pages
        ]
    )

    if not relevant_external_pages:
        logger.info(
            "[GetRelevantExternalPages] No pages with embeddings found from link-exchange projects"
        )
        return ProjectPage.objects.none()

    logger.info(
        "[GetRelevantExternalPages] Successfully found relevant external pages",
        num_relevant_pages=len(relevant_external_pages),
        max_pages=max_pages,
        meta_description_preview=meta_description[:100],
    )

    return relevant_external_pages
