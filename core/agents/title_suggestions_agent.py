import re
from collections import Counter

from pydantic_ai import Agent, RunContext

from core.agents.schemas import TitleSuggestionContext, TitleSuggestions
from core.agents.system_prompts import add_todays_date
from core.choices import ContentType, get_default_ai_model
from core.prompts import TITLE_SUGGESTION_SYSTEM_PROMPTS

TITLE_FORMAT_HOW_TO = "how_to"
TITLE_FORMAT_NUMBERED_LIST = "numbered_list"
TITLE_FORMAT_QUESTION = "question"
TITLE_FORMAT_GUIDE = "guide"
TITLE_FORMAT_COMPARISON = "comparison"
TITLE_FORMAT_STATEMENT = "statement"

TITLE_FORMAT_DISPLAY_NAMES = {
    TITLE_FORMAT_HOW_TO: "How-to",
    TITLE_FORMAT_NUMBERED_LIST: "Numbered list",
    TITLE_FORMAT_QUESTION: "Question",
    TITLE_FORMAT_GUIDE: "Guide/Playbook",
    TITLE_FORMAT_COMPARISON: "Comparison",
    TITLE_FORMAT_STATEMENT: "Direct statement",
}

TITLE_FORMAT_PRIORITY_ORDER = [
    TITLE_FORMAT_HOW_TO,
    TITLE_FORMAT_NUMBERED_LIST,
    TITLE_FORMAT_QUESTION,
    TITLE_FORMAT_GUIDE,
    TITLE_FORMAT_COMPARISON,
    TITLE_FORMAT_STATEMENT,
]

OPENING_PHRASE_WORD_COUNT = 3
MAX_OVERUSED_OPENING_PHRASES = 5


def normalize_title_text(title: str) -> str:
    """Normalize spacing for stable title analysis."""
    return re.sub(r"\s+", " ", title.strip())


def classify_title_format(title: str) -> str:
    """Detect a title's high-level structure for variability guidance."""
    normalized_title = normalize_title_text(title).lower()

    if not normalized_title:
        return TITLE_FORMAT_STATEMENT

    if normalized_title.startswith("how to "):
        return TITLE_FORMAT_HOW_TO

    if re.match(r"^\d+\b", normalized_title):
        return TITLE_FORMAT_NUMBERED_LIST

    question_starters = ("why ", "what ", "when ", "where ", "who ", "which ", "can ", "should ")
    if normalized_title.endswith("?") or normalized_title.startswith(question_starters):
        return TITLE_FORMAT_QUESTION

    if " vs " in normalized_title or " versus " in normalized_title or "compare" in normalized_title:
        return TITLE_FORMAT_COMPARISON

    if "guide" in normalized_title or "playbook" in normalized_title or "blueprint" in normalized_title:
        return TITLE_FORMAT_GUIDE

    return TITLE_FORMAT_STATEMENT


def extract_opening_phrase(title: str, opening_phrase_word_count: int = OPENING_PHRASE_WORD_COUNT) -> str:
    """Return the first few words of a title for repetition analysis."""
    normalized_title = normalize_title_text(title).lower()
    sanitized_title = re.sub(r"[^a-z0-9\s]", " ", normalized_title)
    opening_words = [word for word in sanitized_title.split() if word][:opening_phrase_word_count]
    return " ".join(opening_words)


def collect_historical_titles(title_suggestion_context: TitleSuggestionContext) -> list[str]:
    """Collect and de-duplicate previously generated titles across feedback states."""
    historical_titles = []
    suggestion_groups = (
        title_suggestion_context.neutral_suggestions or [],
        title_suggestion_context.liked_suggestions or [],
        title_suggestion_context.disliked_suggestions or [],
    )

    for suggestion_group in suggestion_groups:
        for raw_title in suggestion_group:
            normalized_title = normalize_title_text(raw_title)
            if normalized_title:
                historical_titles.append(normalized_title)

    return list(dict.fromkeys(historical_titles))


def get_overused_opening_phrases(historical_titles: list[str]) -> list[str]:
    """Find opening phrases repeated across historical titles."""
    opening_phrase_usage_counter = Counter()

    for historical_title in historical_titles:
        opening_phrase = extract_opening_phrase(historical_title)
        if opening_phrase:
            opening_phrase_usage_counter[opening_phrase] += 1

    sorted_opening_phrases = sorted(
        opening_phrase_usage_counter.items(), key=lambda phrase_item: (-phrase_item[1], phrase_item[0])
    )

    repeated_opening_phrases = [
        opening_phrase
        for opening_phrase, usage_count in sorted_opening_phrases
        if usage_count > 1
    ]

    return repeated_opening_phrases[:MAX_OVERUSED_OPENING_PHRASES]


def get_underused_formats(
    historical_titles: list[str], number_of_titles_to_generate: int
) -> tuple[list[str], Counter]:
    """Return title formats that have been used least in historical suggestions."""
    format_usage_counter = Counter(classify_title_format(title) for title in historical_titles)

    format_priority_index = {
        format_name: index for index, format_name in enumerate(TITLE_FORMAT_PRIORITY_ORDER)
    }
    formats_sorted_by_usage = sorted(
        TITLE_FORMAT_PRIORITY_ORDER,
        key=lambda format_name: (
            format_usage_counter[format_name],
            format_priority_index[format_name],
        ),
    )

    format_count_to_select = min(number_of_titles_to_generate, len(TITLE_FORMAT_PRIORITY_ORDER))
    underused_formats = formats_sorted_by_usage[:format_count_to_select]

    return underused_formats, format_usage_counter


def build_title_variability_guidance(title_suggestion_context: TitleSuggestionContext) -> str:
    """Build prompt instructions that force more title variety and freshness."""
    number_of_titles_to_generate = max(title_suggestion_context.num_titles, 1)
    historical_titles = collect_historical_titles(title_suggestion_context)
    underused_formats, format_usage_counter = get_underused_formats(
        historical_titles, number_of_titles_to_generate
    )
    overused_opening_phrases = get_overused_opening_phrases(historical_titles)

    preferred_format_lines = "\n".join(
        f"- {TITLE_FORMAT_DISPLAY_NAMES[format_name]} (used {format_usage_counter[format_name]} times)"
        for format_name in underused_formats
    )

    if number_of_titles_to_generate > 1:
        batch_diversity_instruction = f"""
            You are generating {number_of_titles_to_generate} titles in this batch.
            Use different title formats for each title whenever possible.
            The first {OPENING_PHRASE_WORD_COUNT} words must be different across titles in this batch.
            Vary the angle across titles (e.g., tactical, strategic, mistakes-to-avoid, comparison, contrarian).
        """
    else:
        batch_diversity_instruction = """
            You are generating one title.
            Pick a format that is underused historically and avoid repetitive phrasing patterns.
            Prioritize a fresh angle that has not already been overused.
        """

    if overused_opening_phrases:
        overused_opening_phrase_lines = "\n".join(
            f"- {opening_phrase}" for opening_phrase in overused_opening_phrases
        )
        historical_variability_instruction = f"""
            These opening phrases are overused in previous suggestions.
            Do not start a new title with any of them:
            {overused_opening_phrase_lines}
        """
    else:
        historical_variability_instruction = """
            No repeated opening phrases were detected yet.
            Still ensure fresh wording and avoid formulaic repetition.
        """

    return f"""
        IMPORTANT VARIABILITY REQUIREMENTS (HIGH PRIORITY)

        {batch_diversity_instruction}

        Prioritize these underused title formats in this generation:
        {preferred_format_lines}

        {historical_variability_instruction}

        Never produce minor rewrites of previously generated titles.
    """


def create_title_suggestions_agent(content_type=ContentType.SHARING, model=None):  # noqa: C901
    """Create and configure a title suggestions agent for a specific content type."""
    agent = Agent(
        model or get_default_ai_model(),
        output_type=TitleSuggestions,
        deps_type=TitleSuggestionContext,
        system_prompt=TITLE_SUGGESTION_SYSTEM_PROMPTS[content_type],
        retries=2,
        model_settings={"temperature": 0.9},
    )

    agent.system_prompt(add_todays_date)

    @agent.system_prompt
    def add_project_details(ctx: RunContext[TitleSuggestionContext]) -> str:
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

    @agent.system_prompt
    def add_number_of_titles_to_generate(ctx: RunContext[TitleSuggestionContext]) -> str:
        return f"""IMPORTANT: Generate only {ctx.deps.num_titles} titles."""

    @agent.system_prompt
    def add_variability_requirements(ctx: RunContext[TitleSuggestionContext]) -> str:
        return build_title_variability_guidance(ctx.deps)

    @agent.system_prompt
    def add_language_specification(ctx: RunContext[TitleSuggestionContext]) -> str:
        project = ctx.deps.project_details
        return f"""
            IMPORTANT: Generate all titles in {project.language} language.
            Make sure the titles are grammatically correct and culturally
            appropriate for {project.language}-speaking audiences.
        """

    @agent.system_prompt
    def add_user_prompt(ctx: RunContext[TitleSuggestionContext]) -> str:
        if not ctx.deps.user_prompt:
            return ""

        return f"""
            IMPORTANT USER REQUEST: The user has specifically requested the following:
            "{ctx.deps.user_prompt}"

            This is a high-priority requirement. Make sure to incorporate this guidance
            when generating titles while still maintaining SEO best practices and readability.
        """

    @agent.system_prompt
    def add_custom_post_type_prompt(ctx: RunContext[TitleSuggestionContext]) -> str:
        custom_prompt = (ctx.deps.custom_post_type_prompt or "").strip()
        if not custom_prompt:
            return ""

        return f"""
            CUSTOM POST TYPE REQUIREMENT (HIGH PRIORITY)
            The user selected a custom post type with this guidance:
            "{custom_prompt}"

            Make the post type influence clearly visible in title framing, tone, and format.
            Keep titles specific, readable, and SEO-appropriate.
        """

    @agent.system_prompt
    def add_feedback_history(ctx: RunContext[TitleSuggestionContext]) -> str:
        feedback_sections = []

        if ctx.deps.neutral_suggestions:
            neutral = "\n".join(f"- {title}" for title in ctx.deps.neutral_suggestions)
            feedback_sections.append(
                f"""
                Previously Generated Title Suggestions (neutral):
                {neutral}
            """
            )

        if ctx.deps.liked_suggestions:
            liked = "\n".join(f"- {title}" for title in ctx.deps.liked_suggestions)
            feedback_sections.append(
                f"""
                Liked Title Suggestions:
                {liked}
            """
            )

        if ctx.deps.disliked_suggestions:
            disliked = "\n".join(f"- {title}" for title in ctx.deps.disliked_suggestions)
            feedback_sections.append(
                f"""
                Disliked Title Suggestions:
                {disliked}
            """
            )

        if feedback_sections:
            feedback_sections.append(
                """
                Use this feedback to guide your title generation.
                Create titles that are thematically similar to the "Liked" titles,
                and avoid any stylistic or thematic patterns from the "Disliked" titles.

                IMPORTANT!
                You must generate completely new and unique titles.
                Do not repeat or create minor variations of any titles listed above in the
                "Previously Generated", "Liked", or "Disliked" sections.
                Your primary goal is originality.
                """
            )

        return "\n".join(feedback_sections)

    return agent
