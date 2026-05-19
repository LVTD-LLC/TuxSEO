from urllib.parse import urljoin

from django.conf import settings
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from core.agents.schemas import CompetitorVsPostContext
from core.agents.system_prompts import add_project_pages, markdown_lists
from core.choices import AIModel

DEFAULT_COMPARISON_PATH_SUFFIXES = (
    "",
    "pricing",
    "docs",
    "features",
    "integrations",
    "use-cases",
)


def build_research_targets(base_url: str) -> list[str]:
    """Build deterministic key-page targets for product comparison research."""
    normalized_base_url = base_url.rstrip("/") + "/"
    return [urljoin(normalized_base_url, suffix) for suffix in DEFAULT_COMPARISON_PATH_SUFFIXES]


def build_competitor_vs_research_brief(context: CompetitorVsPostContext) -> str:
    """Build structured research + writing instructions for comparison posts."""
    project_research_targets = build_research_targets(context.project_url)
    competitor_research_targets = build_research_targets(context.competitor_url)

    explicit_project_pages = "\n".join([f"- {page.url}" for page in context.project_pages])

    if not explicit_project_pages:
        explicit_project_pages = "- (No indexed project pages available; rely on required key-page targets.)"

    return f"""
            Product 1 (Our Product): {context.project_name}
            URL: {context.project_url}
            Description: {context.project_summary}

            Product 2 (Competitor): {context.competitor_name}
            URL: {context.competitor_url}
            Description: {context.competitor_description}

            Blog Post Title: "{context.title}"
            Language: {context.language}

            Research Requirements (must follow):
            1) Research BOTH products across key page categories:
               - Homepage
               - Pricing / plans
               - Documentation / help center
               - Product/features overview
               - Integrations
               - Representative use-case/solution pages
            2) Prioritize the following known pages for {context.project_name} when relevant:
{explicit_project_pages}
            3) Use these deterministic URL targets as starting points while also discovering live equivalents if paths differ.
               {context.project_name} targets:
               {chr(10).join([f"- {url}" for url in project_research_targets])}
               {context.competitor_name} targets:
               {chr(10).join([f"- {url}" for url in competitor_research_targets])}
            4) If a target page does not exist, find the nearest equivalent page on that site and use it.
            5) Ground claims in evidence from pages you actually researched.

            Required Comparison Structure (must follow in this order):
            - What each product is best known for
            - Side-by-side comparison table (core capabilities, pricing approach, onboarding complexity, integrations)
            - Strengths of {context.project_name}
            - Strengths of {context.competitor_name}
            - Trade-offs and limitations (for both)
            - Fit scenarios (who should choose which product, with concrete examples)
            - Recommendation logic (decision framework with clear if/then guidance)
            - Final recommendation (trustworthy, specific, and practical)

            Tone + Positioning Rules:
            - Stay factual and trustworthy. No hype, no dismissive language.
            - Maintain balance: acknowledge genuine strengths of both products.
            - Apply only a mild preference toward {context.project_name}, and only where evidence supports it.
            - Never force biased claims or spammy persuasion.

            Citation Rules:
            - For important factual claims, include inline markdown links to the supporting source pages.
            - Do not use raw citation markers like [1]. Use readable markdown links in sentence context.
        """


def create_competitor_vs_blog_post_agent(model=None):
    """
    Create an agent to generate comparison blog posts between products using web search.

    Args:
        model: Optional AI model to use. Defaults to Perplexity Sonar for web search capabilities.

    Returns:
        Configured Agent instance
    """
    if model is None:
        model = OpenAIChatModel(
            AIModel.PERPLEXITY_SONAR,
            provider=OpenAIProvider(
                base_url="https://api.perplexity.ai",
                api_key=settings.PERPLEXITY_API_KEY,
            ),
        )

    agent = Agent(
        model,
        output_type=str,
        deps_type=CompetitorVsPostContext,
        system_prompt="""
        You are an expert B2B SaaS analyst and content writer specializing in decision-useful comparison posts.

        Generate a comprehensive comparison post between two products that is deeply researched, balanced, and practical.

        Global requirements:
        1. Use current web information for both products.
        2. Keep the post decision-oriented, not promotional fluff.
        3. Include practical nuance: strengths, weaknesses, trade-offs, and fit-by-scenario guidance.
        4. Be mildly favorable toward the user's product only when evidence supports it.
        5. Keep it SEO-friendly with clear headings, concise sections, and scan-friendly formatting.
        6. Write at least 2000 words.
        7. Return ONLY markdown content (no JSON, no code fences).

        Important formatting rules:
        - Do not start with # title. Start directly with intro paragraph(s).
        - Use ## for main headings and ### for subheadings.
        - Include bullet points where helpful.
        - Include at least one side-by-side comparison table.
        - Add links directly in markdown format in natural sentence flow.
        """,
        retries=2,
        model_settings={"max_tokens": 8000, "temperature": 0.5},
    )

    agent.system_prompt(markdown_lists)
    agent.system_prompt(add_project_pages)

    @agent.system_prompt
    def output_format() -> str:
        return """
            IMPORTANT: Return only the text. Don't surround the text with ```markdown or ```.
        """

    @agent.system_prompt
    def links_insertion() -> str:
        return """
            Instead of leaving reference to links in the text (like this 'sample text[1]'), insert the links into the text in markdown format.
            For example, if the text is 'sample text[1]', the link should be inserted like this: '[sample text](https://www.example.com)'.
        """  # noqa: E501

    @agent.system_prompt
    def add_competitor_vs_post_context(ctx) -> str:
        return build_competitor_vs_research_brief(ctx.deps)

    return agent
