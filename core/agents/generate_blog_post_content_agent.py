from pydantic_ai import Agent

from core.agents.schemas import BlogPostGenerationContext, GeneratedBlogPostSchema
from core.agents.system_prompts import (
    add_custom_post_type_guidance,
    add_language_specification,
    add_project_details,
    add_project_pages,
    add_target_keywords,
    add_title_details,
    add_todays_date,
    filler_content,
    markdown_lists,
    post_structure,
    valid_markdown_format,
)
from core.choices import ContentType, get_default_ai_model
from core.prompts import GENERATE_CONTENT_SYSTEM_PROMPTS


def create_generate_blog_post_content_agent(
    content_type: ContentType = ContentType.SHARING, model=None
):
    """
    Create an agent to generate blog post content.

    Args:
        content_type: The type of content to generate (SHARING, ACTIONABLE, THOUGHT_LEADERSHIP).
        model: Optional AI model to use. Defaults to the default AI model.

    Returns:
        Configured Agent instance
    """
    agent = Agent(
        model or get_default_ai_model(),
        output_type=GeneratedBlogPostSchema,
        deps_type=BlogPostGenerationContext,
        system_prompt=GENERATE_CONTENT_SYSTEM_PROMPTS[content_type],
        retries=2,
        model_settings={"max_tokens": 65500, "temperature": 0.8},
    )

    agent.system_prompt(add_project_details)
    agent.system_prompt(add_project_pages)
    agent.system_prompt(add_title_details)
    agent.system_prompt(add_todays_date)
    agent.system_prompt(add_language_specification)
    agent.system_prompt(add_custom_post_type_guidance)
    agent.system_prompt(add_target_keywords)
    agent.system_prompt(valid_markdown_format)
    agent.system_prompt(markdown_lists)
    agent.system_prompt(post_structure)
    agent.system_prompt(filler_content)

    return agent
