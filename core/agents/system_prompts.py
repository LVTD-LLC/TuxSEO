from django.utils import timezone
from pydantic_ai import RunContext

from core.agents.schemas import BlogPostGenerationContext, WebPageContent


def add_todays_date() -> str:
    return f"Today's Date: {timezone.now().strftime('%Y-%m-%d')}"


def valid_markdown_format() -> str:
    return """
        IMPORTANT: Generate the content in valid markdown format.
        Make sure the content is formatted correctly with:
          - headings
          - paragraphs
          - lists
          - links
    """


def post_structure() -> str:
    return """
        - Don't start with a title, header or a subheader (#, ##, ###). Instead start with a plain text as intro.
        - Use '##' (h2 headers) for sections of the post where necessary.
        - Don't use 3rd levle subheaders (###) or deeper. That should not be necessary for the post.
    """  # noqa: E501


def markdown_lists() -> str:
    return """
        - Add an empty line before the first item in the list.
        - Use lists for bullet points where necessary.
        - Use numbered lists for steps or instructions.
        - Use nested lists for sub-points.
    """  # noqa: E501


def filler_content() -> str:
    return """
        - Do not add content that needs to be filled in later.
        - No placeholders either. This means no:
          - Image Suggestion: [Image]
          - Link Suggestion: [Link]
          ...
      """


def add_project_details(ctx: RunContext[BlogPostGenerationContext]) -> str:
    project = ctx.deps.project_details
    return f"""
        Project Details:
        - Project Name: {project.name}
        - Project Type: {project.type}
        - Project Summary: {project.summary}
        - Blog Theme: {project.blog_theme}
        - Founders: {project.founders}
        - Key Features: {project.key_features}
        - Target Audience: {project.target_audience_summary}
        - Pain Points: {project.pain_points}
        - Product Usage: {project.product_usage}
    """


def add_project_pages(ctx: RunContext) -> str:
    """
    Generic function to add project pages to any context that has a project_pages attribute.
    Works with BlogPostGenerationContext, CompetitorVsPostContext, and any other context
    that includes project pages.
    """
    pages = ctx.deps.project_pages
    if pages:
        always_use_pages = [page for page in pages if page.always_use]
        optional_pages = [page for page in pages if not page.always_use]

        instruction = ""

        if always_use_pages:
            instruction += """
              REQUIRED PAGES TO LINK:
              The following pages MUST be linked in the content you generate. These are essential pages that should be referenced in the blog post where contextually relevant:

            """  # noqa: E501
            for page in always_use_pages:
                instruction += f"""
                  --------
                  - Title: {page.title}
                  - URL: {page.url}
                  - Description: {page.description}
                  - Summary: {page.summary}
                  --------
                """

        if optional_pages:
            external_pages = [page for page in optional_pages if page.link_source == "external"]
            max_external_links = max(1, len(external_pages)) if external_pages else 0

            instruction += """

              OPTIONAL PAGES (Use Intelligently):
              The following pages are available for linking if they are contextually relevant to the content. Use your judgment to determine which pages would provide value to readers and enhance the blog post. Only include links where they naturally fit and add value:

            """  # noqa: E501

            if external_pages:
                instruction += f"""
              EXTERNAL AUTHORITY LINKING RULES:
              - Prefer external links only when they directly support a specific claim or statistic.
              - Keep external links readable and sparse (up to {max_external_links} external links total).
              - Never force an external link just to hit a quota; skip if relevance is weak.
                """

            for page in optional_pages:
                instruction += f"""
                  --------
                  - Title: {page.title}
                  - URL: {page.url}
                  - Description: {page.description}
                  - Summary: {page.summary}
                  - Source: {page.link_source}
                  --------
                """

        return instruction
    else:
        return ""


def add_title_details(ctx: RunContext[BlogPostGenerationContext]) -> str:
    title = ctx.deps.title_suggestion
    return f"""
        This is the title suggestion gnerate by AI using project information:
        - Title: {title.title}
        - Description: {title.description}
        - Category: {title.category}
        - Target Keywords: {
        ", ".join(title.target_keywords) if title.target_keywords else "None specified"
    }
        - Suggested Meta Description: {
        title.suggested_meta_description if title.suggested_meta_description else "None specified"
    }
    """


def add_language_specification(ctx: RunContext[BlogPostGenerationContext]) -> str:
    return f"""
        IMPORTANT: Generate the content in {ctx.deps.project_details.language} language.
        Make sure the content is grammatically correct and culturally appropriate for
        {ctx.deps.project_details.language}-speaking audiences.
    """


def add_target_keywords(ctx: RunContext[BlogPostGenerationContext]) -> str:
    if ctx.deps.project_keywords:
        keywords_list = ", ".join(ctx.deps.project_keywords)
        return f"""
            Focus Keywords for SEO
            The user wants to focus on these specific keywords in the blog post:
            {keywords_list}

            Please incorporate these keywords naturally throughout the content where appropriate.
            Use them as part of normal prose (sentences and headings) where they genuinely fit contextually.
            Do not force awkward repetition or keyword stuffing.
            Never wrap target keywords in visual formatting such as backticks, bold, italics, or quotes just to highlight them.
            Keep target keywords as a regular part of the text so readability stays natural.
        """  # noqa: E501
    else:
        return ""


def add_webpage_content(ctx: RunContext[WebPageContent]) -> str:
    return (
        "Web page content:"
        f"Title: {ctx.deps.title}"
        f"Description: {ctx.deps.description}"
        f"Content: {ctx.deps.markdown_content}"
    )
