from pydantic_ai import Agent

from core.agents.schemas import LinkInsertionContext
from core.choices import get_default_ai_model


def create_insert_links_agent(model=None):
    """
    Create an agent to insert links into blog post content organically.

    Args:
        model: Optional AI model to use. Defaults to GPT-4o.

    Returns:
        Configured Agent instance
    """
    if model is None:
        model = get_default_ai_model()

    agent = Agent(
        model,
        output_type=str,
        deps_type=LinkInsertionContext,
        system_prompt="""
        You are an expert content editor specializing in organic link insertion.

        Your task is to insert links from the provided project pages into the blog post content.
        The links should be inserted naturally and organically where they add value to the reader.

        CRITICAL RULES:
        1. DO NOT edit, rewrite, or change ANY of the existing blog post content
        2. DO NOT add new paragraphs or sentences
        3. DO NOT remove any existing content
        4. DO NOT remove any existing links from the document
        5. DO NOT change, update, or correct ANY URLs of existing links - URLs must remain EXACTLY the same
        6. ONLY insert NEW markdown links from the provided project pages into existing text where appropriate
        7. Insert links by wrapping relevant existing text with markdown link syntax: [text](url)
        8. Each project page should be linked AT MOST ONCE in the entire post
        9. Only insert links where they are contextually relevant and add value
        10. Return ONLY the markdown content, no JSON or structured output
        11. When both internal and external link candidates are provided, include both types in the final post when contextually possible

        HANDLING EXISTING LINKS - FORMATTING CORRECTION ONLY:
        - You MAY convert reference-style links to inline links for better formatting
        - Example: "Machine learning improves accuracy [Study, 2024](https://example.com)."
          → "Machine learning [improves accuracy](https://example.com)."
        - Example: "According to research [1](https://example.com)"
          → "According to [research](https://example.com)"
        - CRITICAL: The URL must remain EXACTLY the same - do NOT change, update, or correct the URL
        - You may change the anchor text to be more natural, but the URL must stay identical
        - Do NOT remove reference-style links without converting them to inline links
        - Do NOT keep reference format like [Source, Year] or [1], [2], etc. - convert to natural inline links

        PRESERVING EXISTING LINK URLS:
        - ALL existing link URLs in the document must remain EXACTLY as they are
        - Do NOT update or correct URLs of existing links
        - Do NOT change URLs to fix broken links or update them
        - If a link URL is "https://old-url.com", it must stay "https://old-url.com" exactly
        - Only the formatting (reference-style to inline) and anchor text can change, never the URL
        - Only insert links from the provided project pages that are NOT already in the document

        Link Insertion Guidelines:
        - Look for phrases or sentences where linking would naturally enhance the content
        - Link relevant keywords or phrases that relate to the project page content
        - Ensure the anchor text is descriptive and natural
        - Distribute links throughout the post (not all in one section)
        - Prefer linking in body paragraphs over headers or introduction
        - Only insert links from the provided project pages list

        Example of what TO DO - Inserting new links:
        Original: "This feature helps you track your website performance."
        With Link: "This feature helps you [track your website performance](https://example.com/analytics)."

        Example of what TO DO - Formatting correction (URL stays the same):
        Original: "Machine learning improves accuracy [Study, 2024](https://example.com)."
        Converted: "Machine learning [improves accuracy](https://example.com)."
        Note: URL "https://example.com" remains EXACTLY the same, only formatting changed

        Example of what TO DO - Both formatting correction and new link insertion:
        Original: "According to research [1](https://existing-link.com), this is important."
        Result: "According to [research](https://existing-link.com), this is [important](https://new-provided-link.com)."
        Note: Existing link URL "https://existing-link.com" stays the same, only formatting changed. New link added.

        Example of what NOT TO DO:
        - DO NOT add: "Learn more about our features [here](url)" if that sentence wasn't there
        - DO NOT change: "This is great" to "This is amazing" even with a link
        - DO NOT restructure or reformat the content
        - DO NOT change URLs: "[text](https://old-url.com)" → "[text](https://new-url.com)" (WRONG!)
        - DO NOT update or correct broken URLs - leave them exactly as they are
        - DO NOT remove existing links without converting them to inline format
        """,  # noqa: E501
        retries=2,
        model_settings={"max_tokens": 65500, "temperature": 0.1},
    )

    @agent.system_prompt
    def add_link_insertion_context(ctx) -> str:
        context: LinkInsertionContext = ctx.deps

        internal_pages = [page for page in context.project_pages if page.link_source == "internal"]
        external_pages = [page for page in context.project_pages if page.link_source == "external"]

        def _format_pages(pages):
            pages_info = ""
            for index, page in enumerate(pages, start=1):
                pages_info += f"""
                Page {index}:
                - URL: {page.url}
                - Title: {page.title}
                - Description: {page.description}
                - Summary: {page.summary}
                """
            return pages_info or "(none)"

        link_mix_instruction = (
            "Use at least 1 internal and 1 external link when contextually relevant. "
            "If only one source type is available, use links from that source only."
            if internal_pages and external_pages
            else "Only one link source is available; insert relevant links from the provided list."
        )

        return f"""
            INTERNAL PROJECT PAGES TO LINK:
            {_format_pages(internal_pages)}

            EXTERNAL PROJECT PAGES TO LINK:
            {_format_pages(external_pages)}

            BLOG POST CONTENT TO INSERT LINKS INTO:
            {context.blog_post_content}

            INSTRUCTIONS:
            1. Insert links from the project pages above organically into the blog post
            2. {link_mix_instruction}
            3. Convert reference-style links (like "text [Source, Year](url)" or "text [1](url)") to natural inline links (like "[text](url)")
            4. CRITICAL: When converting reference-style links, the URL must remain EXACTLY the same - do NOT change, update, or correct the URL
            5. DO NOT remove any existing links from the document
            6. DO NOT change, update, or correct ANY URLs of existing links - URLs must stay identical
            7. Only insert NEW links from the provided project pages that are not already present
            8. Do not change the content, only format existing links and insert new links from the provided project pages
            9. Return only the markdown content with links properly formatted and new links inserted
        """  # noqa: E501

    return agent
