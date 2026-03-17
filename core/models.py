import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from urllib.request import urlopen

import replicate
import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MaxLengthValidator
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django_q.tasks import async_task
from pgvector.django import HnswIndex, VectorField

from core.agents import (
    create_analyze_competitor_agent,
    create_analyze_project_agent,
    create_competitor_vs_blog_post_agent,
    create_extract_competitors_data_agent,
    create_extract_links_agent,
    create_find_competitors_agent,
    create_generate_blog_post_content_agent,
    create_insert_links_agent,
    create_populate_competitor_details_agent,
    create_summarize_page_agent,
    create_title_suggestions_agent,
)
from core.agents.schemas import (
    BlogPostGenerationContext,
    CompetitorAnalysisContext,
    CompetitorDetails,
    GeneratedBlogPostSchema,
    LinkInsertionContext,
    ProjectDetails,
    ProjectPageContext,
    TitleSuggestion,
    TitleSuggestionContext,
    WebPageContent,
)
from core.analytics import ANALYTICS_EVENTS
from core.base_models import BaseModel
from core.choices import (
    BlogPostStatus,
    Category,
    CompetitorPostGenerationStatus,
    ContentType,
    EmailType,
    ExecutionJobOperation,
    ExecutionJobStatus,
    KeywordDataSource,
    Language,
    OGImageStyle,
    ProfileStates,
    ProjectPageSource,
    ProjectPageType,
    ProjectStyle,
    ProjectType,
)
from core.utils import (
    generate_random_key,
    get_jina_embedding,
    get_external_authority_link_candidates,
    get_markdown_content,
    get_og_image_prompt,
    get_relevant_external_pages_for_blog_post,
    get_relevant_pages_for_blog_post,
    process_generated_blog_content,
    run_agent_synchronously,
)
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


class Profile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    key = models.CharField(max_length=64, unique=True, default=generate_random_key)
    experimental_features = models.BooleanField(default=False)

    subscription = models.ForeignKey(
        "djstripe.Subscription",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profile",
        help_text="The user's Stripe Subscription object, if it exists",
    )
    product = models.ForeignKey(
        "djstripe.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profile",
        help_text="The user's Stripe Product object, if it exists",
    )
    customer = models.ForeignKey(
        "djstripe.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profile",
        help_text="The user's Stripe Customer object, if it exists",
    )

    state = models.CharField(
        max_length=255,
        choices=ProfileStates.choices,
        default=ProfileStates.STRANGER,
        help_text="The current state of the user's profile",
    )

    def __str__(self):
        return f"{self.user.username}"

    def track_state_change(self, to_state, metadata=None):
        async_task(
            "core.tasks.track_state_change",
            profile_id=self.id,
            from_state=self.current_state,
            to_state=to_state,
            metadata=metadata,
            source_function="Profile - track_state_change",
            group="Track State Change",
        )

    @property
    def current_state(self):
        if not self.state_transitions.all().exists():
            return ProfileStates.STRANGER
        latest_transition = self.state_transitions.latest("created_at")
        return latest_transition.to_state

    @property
    def has_product_or_subscription(self):
        return self.user.is_superuser or self.product is not None or self.subscription is not None

    @property
    def number_of_active_projects(self):
        return self.projects.count()

    @property
    def number_of_generated_blog_posts(self):
        projects = self.projects.all()
        return sum(project.generated_blog_posts.count() for project in projects)

    @property
    def number_of_generated_blog_posts_this_month(self):
        now = timezone.now()
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        projects = self.projects.all()
        blog_post_count = 0
        for project in projects:
            blog_post_count += project.generated_blog_posts.filter(
                created_at__gte=first_day_of_month
            ).count()
        return blog_post_count

    @property
    def number_of_title_suggestions(self):
        projects = self.projects.all()
        return sum(project.blog_post_title_suggestions.count() for project in projects)

    @property
    def number_of_title_suggestions_this_month(self):
        now = timezone.now()
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        projects = self.projects.all()
        suggestion_count = 0
        for project in projects:
            suggestion_count += project.blog_post_title_suggestions.filter(
                created_at__gte=first_day_of_month
            ).count()
        return suggestion_count

    @property
    def product_name(self):
        if self.user.is_superuser:
            return "Pro"
        if self.product and hasattr(self.product, "name"):
            return self.product.name
        return "Free"

    @property
    def is_on_free_plan(self):
        return self.product_name == "Free" and not self.user.is_superuser

    @property
    def is_on_pro_plan(self):
        if self.user.is_superuser:
            return True
        product_name_lower = self.product_name.lower()
        return "pro" in product_name_lower

    @property
    def project_limit(self):
        if self.is_on_pro_plan:
            return None
        return 1

    @property
    def title_suggestion_limit(self):
        if self.is_on_free_plan:
            return 10
        return None

    @property
    def blog_post_generation_limit(self):
        if self.is_on_free_plan:
            return 3
        return None

    @property
    def has_auto_posting_enabled(self):
        return not self.is_on_free_plan

    @property
    def keyword_limit_per_month(self):
        if self.is_on_free_plan:
            return 0
        return None

    @property
    def number_of_keywords_added_this_month(self):
        now = timezone.now()
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        projects = self.projects.all()
        keyword_count = 0
        for project in projects:
            keyword_count += project.project_keywords.filter(
                date_associated__gte=first_day_of_month
            ).count()
        return keyword_count

    @property
    def reached_keyword_limit(self):
        limit = self.keyword_limit_per_month
        if limit is None:
            return False
        return self.number_of_keywords_added_this_month >= limit

    @property
    def can_add_keywords(self):
        return not self.reached_keyword_limit

    @property
    def reached_project_creation_limit(self):
        limit = self.project_limit
        if limit is None:
            return False
        return self.number_of_active_projects >= limit

    @property
    def reached_title_generation_limit(self):
        limit = self.title_suggestion_limit
        if limit is None:
            return False
        return self.number_of_title_suggestions_this_month >= limit

    @property
    def reached_content_generation_limit(self):
        limit = self.blog_post_generation_limit
        if limit is None:
            return False
        return self.number_of_generated_blog_posts_this_month >= limit

    @property
    def can_create_project(self):
        return not self.reached_project_creation_limit

    @property
    def can_generate_title_suggestions(self):
        return not self.reached_title_generation_limit

    @property
    def can_generate_blog_posts(self):
        return not self.reached_content_generation_limit

    @property
    def competitor_limit(self):
        """Maximum number of competitors a user can have across all projects."""
        if self.is_on_free_plan:
            return 5
        return None

    @property
    def competitor_posts_limit(self):
        """Maximum number of competitor VS blog posts a user can generate."""
        if self.is_on_free_plan:
            return 3
        return None

    @property
    def number_of_competitors(self):
        """Total number of competitors across all projects."""
        projects = self.projects.all()
        return sum(project.competitors.count() for project in projects)

    @property
    def number_of_competitor_posts_generated(self):
        """Total number of competitor VS blog posts that have been generated."""
        projects = self.projects.all()
        competitor_posts_count = 0
        for project in projects:
            competitor_posts_count += (
                project.competitors.filter(blog_post__isnull=False).exclude(blog_post="").count()
            )
        return competitor_posts_count

    @property
    def reached_competitor_limit(self):
        limit = self.competitor_limit
        if limit is None:
            return False
        return self.number_of_competitors >= limit

    @property
    def reached_competitor_posts_limit(self):
        limit = self.competitor_posts_limit
        if limit is None:
            return False
        return self.number_of_competitor_posts_generated >= limit

    @property
    def can_add_competitors(self):
        return not self.reached_competitor_limit

    @property
    def can_generate_competitor_posts(self):
        return not self.reached_competitor_posts_limit

    def get_or_create_project(self, url: str, source: str = None) -> "Project":
        project, created = Project.objects.get_or_create(profile=self, url=url)

        project_metadata = {
            "source": source,
            "profile_id": self.id,
            "profile_email": self.user.email,
            "project_id": project.id,
            "project_name": project.name,
            "project_url": url,
        }

        if created:
            async_task(
                "core.tasks.track_event",
                profile_id=self.id,
                event_name=ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED,
                properties=project_metadata,
                source_function="Profile.get_or_create_project",
                group="Track Event",
            )
            logger.info("[Get or Create Project] Project created", **project_metadata)
        else:
            logger.info("[Get or Create Project] Got existing project", **project_metadata)

        return project


class ProfileStateTransition(BaseModel):
    profile = models.ForeignKey(
        Profile, null=True, blank=True, on_delete=models.SET_NULL, related_name="state_transitions"
    )
    from_state = models.CharField(max_length=255, choices=ProfileStates.choices)
    to_state = models.CharField(max_length=255, choices=ProfileStates.choices)
    backup_profile_id = models.IntegerField()
    metadata = models.JSONField(null=True, blank=True)


class BlogPost(BaseModel):
    title = models.CharField(max_length=250)
    description = models.TextField(blank=True)
    slug = models.SlugField(max_length=250)
    tags = models.TextField()
    content = models.TextField()
    icon = models.ImageField(upload_to="blog_post_icons/", blank=True)
    image = models.ImageField(upload_to="blog_post_images/", blank=True)

    status = models.CharField(
        max_length=20,
        choices=BlogPostStatus.choices,
        default=BlogPostStatus.DRAFT,
    )

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("blog_post", kwargs={"slug": self.slug})


class Project(BaseModel):
    profile = models.ForeignKey(
        Profile, null=True, blank=True, on_delete=models.CASCADE, related_name="projects"
    )
    url = models.URLField(max_length=200)
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=50, choices=ProjectType.choices, default=ProjectType.SAAS)
    summary = models.TextField(blank=True)

    # Agent Settings
    enable_automatic_post_submission = models.BooleanField(default=False)
    enable_automatic_post_generation = models.BooleanField(default=False)
    enable_automatic_og_image_generation = models.BooleanField(default=False)
    og_image_style = models.CharField(
        max_length=50,
        choices=OGImageStyle.choices,
        default=OGImageStyle.MODERN_GRADIENT,
        blank=True,
    )

    particiate_in_link_exchange = models.BooleanField(default=False)

    # Sitemap
    sitemap_url = models.URLField(max_length=500, blank=True, default="")

    # Content from Jina Reader
    date_scraped = models.DateTimeField(null=True, blank=True)
    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    markdown_content = models.TextField(blank=True, default="")

    # AI Content
    date_analyzed = models.DateTimeField(null=True, blank=True)
    blog_theme = models.TextField(blank=True)
    founders = models.TextField(blank=True)
    key_features = models.TextField(blank=True)
    language = models.CharField(max_length=50, choices=Language.choices, default=Language.ENGLISH)
    target_audience_summary = models.TextField(blank=True)
    pain_points = models.TextField(blank=True)
    product_usage = models.TextField(blank=True)
    links = models.TextField(blank=True)
    competitors_list = models.TextField(blank=True)
    style = models.CharField(
        max_length=50, choices=ProjectStyle.choices, default=ProjectStyle.DIGITAL_ART
    )
    proposed_keywords = models.TextField(blank=True)
    location = models.CharField(max_length=50, default="Global")

    def __str__(self):
        return self.name

    @property
    def project_desctiption_string_for_ai(self):
        return f"""
        Project Description:
        - Project Name: {self.name}
        - Project Type: {self.type}
        - Project Summary: {self.summary}
        - Blog Theme: {self.blog_theme}
        - Founders: {self.founders}
        - Key Features: {self.key_features}
        - Target Audience: {self.target_audience_summary}
        - Pain Points: {self.pain_points}
        - Product Usage: {self.product_usage}
        """

    @property
    def project_details(self):
        return ProjectDetails(
            name=self.name,
            type=self.type,
            summary=self.summary,
            blog_theme=self.blog_theme,
            founders=self.founders,
            key_features=self.key_features,
            target_audience_summary=self.target_audience_summary,
            pain_points=self.pain_points,
            product_usage=self.product_usage,
            links=self.links,
            language=self.language,
            proposed_keywords=self.proposed_keywords,
            location=self.location,
        )

    @property
    def title_suggestions(self):
        return self.blog_post_title_suggestions.all()

    @property
    def liked_title_suggestions(self):
        return self.blog_post_title_suggestions.filter(user_score__gt=0).all()

    @property
    def disliked_title_suggestions(self):
        return self.blog_post_title_suggestions.filter(user_score__lt=0).all()

    @property
    def neutral_title_suggestions(self):
        return self.blog_post_title_suggestions.filter(user_score=0).all()

    @property
    def generated_blog_posts(self):
        return self.generated_blog_posts.all()

    @property
    def last_posted_blog_post(self):
        generated_blog_posts = self.generated_blog_posts
        if generated_blog_posts.exists():
            return (
                generated_blog_posts.filter(posted=True, date_posted__isnull=False)
                .order_by("-date_posted")
                .first()
            )
        return None

    @property
    def has_auto_submission_setting(self):
        return self.auto_submission_settings.exists()

    def get_page_content(self):
        """
        Fetch page content using Jina Reader API and update the project.
        Returns the content if successful, raises ValueError otherwise.
        """
        title, description, markdown_content = get_markdown_content(self.url)

        if not markdown_content:
            logger.error(
                "[Get Page Content] Failed to get page content",
                url=self.url,
            )
            return False

        self.date_scraped = timezone.now()
        self.title = title
        self.description = description
        self.markdown_content = markdown_content

        self.save(
            update_fields=[
                "date_scraped",
                "title",
                "description",
                "markdown_content",
            ]
        )

        return True

    def analyze_content(self):
        """
        Analyze the page content using PydanticAI and update project details.
        Should be called after get_page_content().
        """
        agent = create_analyze_project_agent()

        result = run_agent_synchronously(
            agent,
            "Analyze this web page content and extract the key information.",
            deps=WebPageContent(
                title=self.title,
                description=self.description,
                markdown_content=self.markdown_content,
            ),
            function_name="analyze_content",
            model_name="Project",
        )

        self.name = result.output.name
        self.type = result.output.type
        self.summary = result.output.summary
        self.blog_theme = result.output.blog_theme
        self.founders = result.output.founders
        self.key_features = result.output.key_features
        self.target_audience_summary = result.output.target_audience_summary
        self.pain_points = result.output.pain_points
        self.product_usage = result.output.product_usage
        self.links = result.output.links
        self.language = result.output.language
        self.proposed_keywords = result.output.proposed_keywords
        self.location = result.output.location
        self.date_analyzed = timezone.now()
        self.save()

        async_task("core.tasks.generate_blog_post_suggestions", self.id)
        async_task("core.tasks.process_project_keywords", self.id)
        async_task("core.tasks.schedule_project_page_analysis", self.id)
        async_task("core.tasks.schedule_project_competitor_analysis", self.id)

        return True

    def generate_title_suggestions(
        self, content_type=ContentType.SHARING, num_titles=3, user_prompt="", model=None
    ):
        agent = create_title_suggestions_agent(content_type=content_type, model=model)

        deps = TitleSuggestionContext(
            project_details=self.project_details,
            num_titles=num_titles,
            user_prompt=user_prompt,
            liked_suggestions=[suggestion.title for suggestion in self.liked_title_suggestions],
            disliked_suggestions=[
                suggestion.title for suggestion in self.disliked_title_suggestions
            ],
            neutral_suggestions=[suggestion.title for suggestion in self.neutral_title_suggestions],
        )

        result = run_agent_synchronously(
            agent,
            "Please generate blog post title suggestions based on the project details.",
            deps=deps,
            function_name="generate_title_suggestions",
            model_name="Project",
        )

        with transaction.atomic():
            suggestions = []
            for title in result.output.titles:
                suggestion = BlogPostTitleSuggestion(
                    project=self,
                    title=title.title,
                    description=title.description,
                    category=title.category,
                    content_type=content_type,
                    target_keywords=title.target_keywords,
                    prompt=user_prompt,
                    suggested_meta_description=title.suggested_meta_description,
                )
                suggestions.append(suggestion)

            created_suggestions = BlogPostTitleSuggestion.objects.bulk_create(suggestions)

            for suggestion in created_suggestions:
                if suggestion.target_keywords:
                    async_task("core.tasks.save_title_suggestion_keywords", suggestion.id)

            return created_suggestions

    def get_a_list_of_links(self, model=None):
        agent = create_extract_links_agent(model)

        result = run_agent_synchronously(
            agent,
            "Please extract all the URLs from this markdown text and return them as a list.",
            deps=self.links,
            function_name="get_a_list_of_links",
            model_name="Project",
        )

        return result.output

    def find_competitors(self):
        agent = create_find_competitors_agent(is_on_free_plan=self.profile.is_on_free_plan)

        result = run_agent_synchronously(
            agent,
            "Give me a list of sites that might be considered my competition.",
            deps=self.project_details,
            function_name="find_competitors",
            model_name="Project",
        )

        self.competitors_list = result.output
        self.save(update_fields=["competitors_list"])

        return result.output

    def get_and_save_list_of_competitors(self, model=None):
        agent = create_extract_competitors_data_agent(model)

        result = run_agent_synchronously(
            agent,
            "Please extract all the competitors from the text provided.",
            deps=self.competitors_list,
            function_name="get_and_save_list_of_competitors",
            model_name="Project",
        )

        competitors = []
        for competitor in result.output:
            competitors.append(
                Competitor(
                    project=self,
                    name=competitor.name,
                    url=competitor.url,
                    description=competitor.description,
                )
            )

        competitors = Competitor.objects.bulk_create(competitors)

        return competitors

    def save_keyword(self, keyword_text: str, use: bool = False):
        keyword_obj, created = Keyword.objects.get_or_create(
            keyword_text=keyword_text,
            country="us",
            data_source=KeywordDataSource.GOOGLE_KEYWORD_PLANNER,
        )

        # Fetch metrics if newly created
        if created:
            metrics_fetched = keyword_obj.fetch_and_update_metrics()
            if not metrics_fetched:
                logger.warning(
                    "[Save Keyword] Failed to fetch metrics for keyword",
                    keyword_id=keyword_obj.id,
                    keyword_text=keyword_text,
                )

        # Associate with project
        project_keyword, pk_created = ProjectKeyword.objects.get_or_create(
            project=self, keyword=keyword_obj
        )

        # Update the use field if specified
        if use and not project_keyword.use:
            project_keyword.use = True
            project_keyword.save(update_fields=["use"])

    def get_keywords(self) -> dict:
        """
        Build a dictionary of project keywords for quick lookup.

        Returns a dict mapping lowercase keyword text to keyword metadata:
        {
            "keyword_text": {
                "keyword": Keyword object,
                "in_use": bool,
                "project_keyword_id": int
            }
        }
        """
        project_keywords = {}
        for project_keyword in self.project_keywords.select_related("keyword").all():
            project_keywords[project_keyword.keyword.keyword_text.lower()] = {
                "keyword": project_keyword.keyword,
                "in_use": project_keyword.use,
                "project_keyword_id": project_keyword.id,
            }
        return project_keywords

    class Meta:
        unique_together = ("profile", "url")




class ProjectCustomPostType(BaseModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="custom_post_types",
    )
    name = models.CharField(max_length=80)
    normalized_name = models.CharField(max_length=80, editable=False)
    prompt_guidance = models.TextField(validators=[MaxLengthValidator(1200)])

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "normalized_name"],
                name="project_custom_post_type_unique_normalized_name",
            )
        ]

    def __str__(self):
        return f"{self.project.name}: {self.name}"

    @staticmethod
    def normalize_name(name: str) -> str:
        return " ".join((name or "").split()).strip().lower()

    def clean(self):
        self.name = " ".join((self.name or "").split()).strip()
        self.normalized_name = self.normalize_name(self.name)

        if not self.name:
            raise ValidationError({"name": "Name cannot be empty."})

        if len(self.name) < 2:
            raise ValidationError({"name": "Name must be at least 2 characters long."})

        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9\s\-/&()]*$", self.name):
            raise ValidationError({
                "name": "Use only letters, numbers, spaces, and - / & ( ) characters.",
            })

        prompt_guidance = (self.prompt_guidance or "").strip()
        if not prompt_guidance:
            raise ValidationError({"prompt_guidance": "Prompt guidance cannot be empty."})

        if len(prompt_guidance) > 1200:
            raise ValidationError({"prompt_guidance": "Prompt guidance must be 1200 characters or less."})

        self.prompt_guidance = prompt_guidance

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class BlogPostTitleSuggestion(BaseModel):
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="blog_post_title_suggestions",
    )

    custom_post_type = models.ForeignKey(
        "ProjectCustomPostType",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="title_suggestions",
    )

    title = models.CharField(max_length=255)
    content_type = models.CharField(
        max_length=20, choices=ContentType.choices, default=ContentType.SHARING
    )
    category = models.CharField(
        max_length=50, choices=Category.choices, default=Category.GENERAL_AUDIENCE
    )
    description = models.TextField()
    prompt = models.TextField(blank=True)
    target_keywords = models.JSONField(default=list, blank=True, null=True)
    suggested_meta_description = models.TextField(blank=True)

    user_score = models.SmallIntegerField(
        default=0,
        choices=[
            (-1, "Didn't Like"),
            (0, "Undecided"),
            (1, "Liked"),
        ],
        help_text="User's rating of the title suggestion",
    )

    archived = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.project.name}: {self.title}"

    @property
    def title_suggestion_string_for_ai(self):
        query = f"- Title: {self.title}\n"
        query += f"- Category: {self.category}\n"
        query += f"- Description: {self.description}\n"
        query += f"- Suggested Meta Description: {self.suggested_meta_description}\n"

        if self.prompt:
            query += f"- Original User Prompt: {self.prompt}\n\n"

        return query

    @property
    def title_suggestion_schema(self):
        return TitleSuggestion(
            title=self.title,
            category=self.category,
            target_keywords=self.target_keywords,
            description=self.description,
            suggested_meta_description=self.suggested_meta_description,
        )

    def get_internal_links(self, max_pages=2):
        manually_selected_project_pages = list(self.project.project_pages.filter(always_use=True))
        relevant_project_pages = list(
            get_relevant_pages_for_blog_post(
                self.project, self.suggested_meta_description, max_pages=max_pages
            )
        )

        all_internal_project_pages = manually_selected_project_pages + relevant_project_pages
        unique_pages_by_url = {}

        for project_page in all_internal_project_pages:
            if project_page.url not in unique_pages_by_url:
                unique_pages_by_url[project_page.url] = project_page

        return list(unique_pages_by_url.values())

    def get_blog_post_keywords(self):
        project_keywords = list(
            self.project.project_keywords.filter(use=True).select_related("keyword")
        )
        project_keyword_texts = [keyword.keyword.keyword_text for keyword in project_keywords]
        post_suggestion_keywords = self.target_keywords or []
        keywords_to_use = list(set(project_keyword_texts + post_suggestion_keywords))

        return keywords_to_use

    def get_external_authority_links(self, max_links: int | None = None):
        target_links = max_links or getattr(settings, "EXTERNAL_AUTHORITY_LINK_TARGET", 2)
        target_links = max(1, min(3, int(target_links)))
        if not self.suggested_meta_description:
            return []

        authority_links = get_external_authority_link_candidates(
            meta_description=self.suggested_meta_description,
            max_links=target_links,
        )

        # Fallback to internal relevance retrieval when Exa is unavailable or sparse.
        if len(authority_links) < target_links:
            existing_urls = {link["url"] for link in authority_links}
            try:
                fallback_pages = get_relevant_external_pages_for_blog_post(
                    meta_description=self.suggested_meta_description,
                    exclude_project=self.project,
                    max_pages=target_links,
                )
            except Exception as error:
                logger.warning(
                    "[ExternalAuthorityLinks] Fallback retrieval failed",
                    title_suggestion_id=self.id,
                    error=str(error),
                )
                fallback_pages = []

            for page in fallback_pages:
                if page.url in existing_urls:
                    continue
                authority_links.append(
                    {
                        "url": page.url,
                        "title": page.title,
                        "description": page.description,
                        "summary": page.summary,
                        "link_source": "external",
                    }
                )
                existing_urls.add(page.url)
                if len(authority_links) >= target_links:
                    break

        return authority_links[:target_links]

    def get_blog_post_generation_context(self, content_type=ContentType.SHARING):
        internal_project_pages = self.get_internal_links()
        external_authority_links = self.get_external_authority_links()

        project_page_contexts = [
            ProjectPageContext(
                url=page.url,
                title=page.title,
                description=page.description,
                summary=page.summary,
                always_use=page.always_use,
                link_source="internal",
            )
            for page in internal_project_pages
        ]

        existing_urls = {page_context.url for page_context in project_page_contexts}
        for link in external_authority_links:
            if link["url"] in existing_urls:
                continue
            project_page_contexts.append(
                ProjectPageContext(
                    url=link["url"],
                    title=link["title"],
                    description=link["description"],
                    summary=link["summary"],
                    always_use=False,
                    link_source="external",
                )
            )

        return BlogPostGenerationContext(
            project_details=self.project.project_details,
            title_suggestion=self.title_suggestion_schema,
            project_keywords=self.get_blog_post_keywords(),
            project_pages=project_page_contexts,
            content_type=content_type,
        )

    @staticmethod
    def contains_placeholder_language(blog_post_content: str) -> bool:
        import re

        placeholder_patterns = [
            r"insert\s+(an?\s+)?(image|screenshot|link|video|chart|graphic)\s+(here|below|above)",
            r"(image|screenshot|link)\s+suggestion",
            r"\[(image|screenshot|link|placeholder|todo|tbd)\]",
            r"\b(todo|tbd|to be added|coming soon)\b",
        ]

        return any(
            re.search(pattern, blog_post_content, re.IGNORECASE)
            for pattern in placeholder_patterns
        )

    @staticmethod
    def has_incomplete_ending(blog_post_content: str) -> bool:
        import re

        normalized_content = (blog_post_content or "").strip()

        if not normalized_content:
            return True

        non_empty_lines = [line.strip() for line in normalized_content.splitlines() if line.strip()]
        if not non_empty_lines:
            return True

        last_line = non_empty_lines[-1]

        if re.search(r"[:;,\-(\[]$", last_line) or last_line.endswith("..."):
            return True

        if re.search(r"\b(and|or|but|because|with|to|for|in|on|at|of|the|a|an)$", last_line.lower()):
            return True

        has_complete_sentence_ending = (
            re.search(r"[.!?](?:[\"'\)\]]+)?$", last_line) is not None
        )

        return not has_complete_sentence_ending

    def _get_generation_target_keywords(self) -> list[str]:
        return [keyword for keyword in self.get_blog_post_keywords() if keyword and keyword.strip()]

    @staticmethod
    def contains_forced_keyword_markdown(
        blog_post_content: str, target_keywords: list[str]
    ) -> tuple[bool, str]:
        import re

        if not blog_post_content:
            return False, ""

        for raw_keyword in target_keywords:
            normalized_keyword = (raw_keyword or "").strip()
            if not normalized_keyword:
                continue

            escaped_keyword = re.escape(normalized_keyword)
            forced_format_patterns = [
                rf"`{escaped_keyword}`",
                rf"\*\*{escaped_keyword}\*\*",
                rf"__{escaped_keyword}__",
                rf"\*{escaped_keyword}\*",
                rf"_{escaped_keyword}_",
            ]

            if any(
                re.search(pattern, blog_post_content, re.IGNORECASE)
                for pattern in forced_format_patterns
            ):
                return (
                    True,
                    f"Target keyword '{normalized_keyword}' is wrapped in markdown emphasis/code. Keep it in plain prose.",
                )

        return False, ""

    @staticmethod
    def has_any_target_keyword_usage(blog_post_content: str, target_keywords: list[str]) -> bool:
        normalized_content = (blog_post_content or "").lower()
        if not normalized_content:
            return False

        return any(
            normalized_keyword in normalized_content
            for normalized_keyword in [
                (keyword or "").strip().lower() for keyword in target_keywords if keyword and keyword.strip()
            ]
        )

    def validate_generated_blog_post_content(self, blog_post_content: str):
        normalized_content = (blog_post_content or "").strip()

        if not normalized_content:
            return False, "Generated content is empty."

        if self.contains_placeholder_language(normalized_content):
            return False, "Generated content includes placeholder language."

        if self.has_incomplete_ending(normalized_content):
            return False, "Generated content appears to be cut off before completion."

        target_keywords = self._get_generation_target_keywords()
        if target_keywords and not self.has_any_target_keyword_usage(normalized_content, target_keywords):
            return False, "Generated content must naturally include at least one selected target keyword."

        has_forced_keyword_markdown, keyword_markdown_error = self.contains_forced_keyword_markdown(
            normalized_content,
            target_keywords,
        )
        if has_forced_keyword_markdown:
            return False, keyword_markdown_error

        return True, ""

    def build_content_generation_prompt(self, previous_validation_error: str = "") -> str:
        target_keywords = self._get_generation_target_keywords()
        prompt_lines = [
            "Generate a complete, publication-ready blog post from the provided context.",
            "Return all required schema fields: description, slug, tags, and content.",
            "The content must be fully written and final.",
            "Never include placeholders or editorial notes (for example: insert image here, add screenshot, add link, [IMAGE], [LINK], TODO, or TBD).",  # noqa: E501
            "Do not include a References section.",
            "Finish with a complete conclusion and end on a complete sentence.",
        ]

        if target_keywords:
            prompt_lines.extend(
                [
                    f"Target keywords to use naturally when relevant: {', '.join(target_keywords)}.",
                    "Integrate keywords as normal prose inside sentences and headings.",
                    "Never wrap target keywords in backticks, bold, italics, or other forced markdown emphasis.",
                    "Avoid keyword stuffing and keep readability natural.",
                ]
            )

        if previous_validation_error:
            prompt_lines.append(
                f"Previous draft failed validation: {previous_validation_error} Regenerate and fully fix this issue."  # noqa: E501
            )

        return "\n".join(prompt_lines)

    def generate_content_with_custom_flow(self, content_type=ContentType.SHARING):
        content_generation_agent = create_generate_blog_post_content_agent(
            content_type=content_type
        )
        blog_post_generation_context = self.get_blog_post_generation_context(content_type)

        maximum_generation_attempts = 3
        latest_validation_error = ""

        for generation_attempt_number in range(1, maximum_generation_attempts + 1):
            generation_prompt = self.build_content_generation_prompt(
                previous_validation_error=latest_validation_error
            )
            generation_result = run_agent_synchronously(
                content_generation_agent,
                generation_prompt,
                deps=blog_post_generation_context,
                function_name="generate_content_with_custom_flow",
                model_name="BlogPostTitleSuggestion",
            )

            generated_blog_post_schema = generation_result.output
            is_content_valid, validation_error = self.validate_generated_blog_post_content(
                generated_blog_post_schema.content
            )

            if is_content_valid:
                return generated_blog_post_schema

            latest_validation_error = validation_error

            logger.warning(
                "[Generate Content Custom Flow] Generated content failed validation",
                title_suggestion_id=self.id,
                project_id=self.project.id,
                generation_attempt_number=generation_attempt_number,
                maximum_generation_attempts=maximum_generation_attempts,
                validation_error=validation_error,
            )

        raise ValueError(
            "Failed to generate a complete blog post without placeholders after multiple attempts."  # noqa: E501
        )

    def generate_content(self, content_type=ContentType.SHARING):
        generated_blog_post_schema = self.generate_content_with_custom_flow(
            content_type=content_type
        )

        generated_content = (generated_blog_post_schema.content or "").strip()
        if not generated_content:
            raise ValueError("Generated blog post content is empty.")

        default_tags = ", ".join(self.target_keywords) if self.target_keywords else ""
        generated_tags = (
            generated_blog_post_schema.tags.strip()
            if generated_blog_post_schema.tags
            else default_tags
        )
        generated_description = (
            generated_blog_post_schema.description.strip()
            if generated_blog_post_schema.description
            else self.suggested_meta_description
        )

        generated_slug_source = generated_blog_post_schema.slug or self.title
        generated_slug = slugify(generated_slug_source) or slugify(self.title)

        blog_post = GeneratedBlogPost.objects.create(
            project=self.project,
            title_suggestion=self,
            title=self.title,
            description=generated_description,
            slug=generated_slug,
            tags=generated_tags,
            content=generated_content,
        )
        blog_post.create_workflow_audit_event(
            checkpoint="CONTENT",
            event_type="CONTENT_GENERATED",
            actor_profile=self.project.profile,
            decision=GeneratedBlogPost.ApprovalStatus.PENDING,
        )

        blog_post.insert_links_into_post()

        blog_post_title, blog_post_content = process_generated_blog_content(
            generated_content=blog_post.content,
            fallback_title=self.title,
            title_suggestion_id=self.id,
            project_id=self.project.id,
        )

        blog_post.title = blog_post_title
        blog_post.slug = slugify(blog_post_title)
        blog_post.content = blog_post_content
        blog_post.save(update_fields=["title", "slug", "content"])

        if self.project.enable_automatic_og_image_generation:
            async_task(
                "core.tasks.generate_og_image_for_blog_post",
                blog_post.id,
                group="Generate OG Image",
            )

        if GeneratedBlogPost.objects.filter(project__profile=self.project.profile).count() == 1:
            async_task(
                "core.tasks.track_event",
                profile_id=self.project.profile.id,
                event_name=ANALYTICS_EVENTS.FIRST_BLOG_GENERATED,
                properties={
                    "project_id": self.project.id,
                    "blog_post_id": blog_post.id,
                    "title_suggestion_id": self.id,
                    "content_type": content_type,
                },
                source_function="BlogPostTitleSuggestion.generate_content",
                group="Track Event",
            )

        return blog_post


class AutoSubmissionSetting(BaseModel):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="auto_submission_settings"
    )
    endpoint_url = models.URLField(
        max_length=500, help_text="The endpoint to which posts will be automatically submitted."
    )
    body = models.JSONField(
        default=dict, blank=True, null=True, help_text="Key-value pairs for the request body."
    )
    header = models.JSONField(
        default=dict, blank=True, null=True, help_text="Key-value pairs for the request headers."
    )
    posts_per_month = models.PositiveIntegerField(
        default=1, help_text="How many posts to publish per month."
    )
    preferred_timezone = models.CharField(  # noqa: DJ001
        max_length=64,
        blank=True,
        null=True,
        help_text="Preferred timezone for publishing posts.",
    )
    preferred_time = models.TimeField(
        blank=True, null=True, help_text="Preferred time of day to publish posts."
    )

    def __str__(self):
        return f"{self.project.name}"


class GeneratedBlogPost(BaseModel):
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="generated_blog_posts",
    )
    title_suggestion = models.ForeignKey(
        BlogPostTitleSuggestion,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="generated_blog_posts",
    )
    title = models.CharField(max_length=250)
    description = models.TextField(blank=True)
    slug = models.SlugField(max_length=250)
    tags = models.TextField()
    content = models.TextField()
    icon = models.ImageField(upload_to="generated_blog_post_icons/", blank=True)
    image = models.ImageField(upload_to="generated_blog_post_images/", blank=True)

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CHANGES_REQUESTED = "CHANGES_REQUESTED", "Changes Requested"

    posted = models.BooleanField(default=False)
    date_posted = models.DateTimeField(null=True, blank=True)
    publish_approval_status = models.CharField(
        max_length=32,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    external_links_approval_status = models.CharField(
        max_length=32,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    publish_review_reason = models.TextField(blank=True, default="")
    external_links_review_reason = models.TextField(blank=True, default="")
    publish_reviewed_at = models.DateTimeField(null=True, blank=True)
    external_links_reviewed_at = models.DateTimeField(null=True, blank=True)
    publish_reviewed_by = models.ForeignKey(
        "Profile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_publish_blog_posts",
    )
    external_links_reviewed_by = models.ForeignKey(
        "Profile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_external_links_blog_posts",
    )

    def __str__(self):
        return f"{self.project.name}: {self.title}"

    def create_workflow_audit_event(
        self,
        *,
        checkpoint: str,
        event_type: str,
        actor_profile=None,
        decision: str = "",
        reason: str = "",
        metadata: dict | None = None,
    ):
        return BlogPostWorkflowAuditLog.objects.create(
            project=self.project,
            generated_blog_post=self,
            checkpoint=checkpoint,
            event_type=event_type,
            actor_profile=actor_profile,
            decision=decision,
            reason=reason,
            metadata=metadata or {},
        )

    def apply_approval_decision(self, *, checkpoint: str, decision: str, actor_profile, reason: str = ""):
        now = timezone.now()
        status_map = {
            "approve": self.ApprovalStatus.APPROVED,
            "reject": self.ApprovalStatus.REJECTED,
            "request_changes": self.ApprovalStatus.CHANGES_REQUESTED,
        }
        new_status = status_map[decision]

        if checkpoint == "publish":
            self.publish_approval_status = new_status
            self.publish_review_reason = reason or ""
            self.publish_reviewed_at = now
            self.publish_reviewed_by = actor_profile
            self.save(
                update_fields=[
                    "publish_approval_status",
                    "publish_review_reason",
                    "publish_reviewed_at",
                    "publish_reviewed_by",
                ]
            )
        else:
            self.external_links_approval_status = new_status
            self.external_links_review_reason = reason or ""
            self.external_links_reviewed_at = now
            self.external_links_reviewed_by = actor_profile
            self.save(
                update_fields=[
                    "external_links_approval_status",
                    "external_links_review_reason",
                    "external_links_reviewed_at",
                    "external_links_reviewed_by",
                ]
            )

        self.create_workflow_audit_event(
            checkpoint=checkpoint.upper(),
            event_type="REVIEW_DECISION",
            actor_profile=actor_profile,
            decision=new_status,
            reason=reason or "",
        )

    @classmethod
    def blog_post_structure_rules(cls):
        return """
        - Use markdown.
        - Start with the title as h1 (#). Do no include any other metadata (description, slug, etc.)
        - Then do and intro, starting with `## Introduction`, then a paragraph of text.
        - Continue with h2 (##) topics as you see fit.
        - Do not go deeper than h2 (##) for post structure.
        - Never inlcude placeholder items (insert image here, link suggestions, etc.)
        - Do not have `References` section, insert all the links into the post directly, organically.
        - Do not include a call to action paragraph at the end of the post.
        - Finish the post with a conclusion.
        - Instead of using links as a reference, try to insert them into the post directly, organically.
        """  # noqa: E501

    @property
    def generated_blog_post_schema(self):
        return GeneratedBlogPostSchema(
            description=self.description,
            slug=self.slug,
            tags=self.tags,
            content=self.content,
        )

    def submit_blog_post_to_endpoint(self):
        from core.utils import replace_placeholders

        project = self.project
        submission_settings = (
            AutoSubmissionSetting.objects.filter(project=project).order_by("-id").first()
        )

        if not submission_settings or not submission_settings.endpoint_url:
            logger.warning(
                "No AutoSubmissionSetting or endpoint_url found for project", project_id=project.id
            )
            return False

        url = submission_settings.endpoint_url
        headers = replace_placeholders(submission_settings.header, self)
        body = replace_placeholders(submission_settings.body, self)

        logger.info(
            "[Submit Blog Post] Submitting blog post to endpoint",
            project_id=project.id,
            profile_id=project.profile.id,
            endpoint_url=url,
            headers_configured=bool(headers),
            body_configured=bool(body),
        )

        try:
            session = requests.Session()
            session.cookies.clear()

            if headers is None:
                headers = {}

            if "content-type" not in headers and "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"

            response = session.post(url, json=body, headers=headers, timeout=15)
            response.raise_for_status()
            return True

        except requests.RequestException as e:
            logger.error(
                "[Submit Blog Post to Endpoint] Request error",
                error=str(e),
                url=url,
                headers=headers,
                exc_info=True,
            )
            return False

    def generate_og_image(self) -> tuple[bool, str]:
        """
        Generate an Open Graph image for a blog post using Replicate flux-schnell model.

        Args:
            generated_post: The GeneratedBlogPost instance to generate an image for
            replicate_api_token: Replicate API token for authentication

        Returns:
            A tuple of (success: bool, message: str)
        """

        if not settings.REPLICATE_API_TOKEN:
            logger.error(
                "[GenerateOGImage] Replicate API token not configured",
                blog_post_id=self.id,
                project_id=self.project_id,
            )
            return False, "Replicate API token not configured"

        if self.image:
            logger.info(
                "[GenerateOGImage] Image already exists for blog post",
                blog_post_id=self.id,
                project_id=self.project_id,
            )
            return True, f"Image already exists for blog post {self.id}"

        try:
            blog_post_category = (
                self.title_suggestion.category if self.title_suggestion.category else "technology"
            )

            project_og_style = self.project.og_image_style or OGImageStyle.MODERN_GRADIENT
            prompt = get_og_image_prompt(project_og_style, blog_post_category)

            logger.info(
                "[GenerateOGImage] Starting image generation",
                blog_post_id=self.id,
                project_id=self.project_id,
                category=blog_post_category,
                og_style=project_og_style,
                prompt=prompt,
            )

            replicate_client = replicate.Client(api_token=settings.REPLICATE_API_TOKEN)

            output = replicate_client.run(
                "black-forest-labs/flux-schnell",
                input={
                    "prompt": prompt,
                    "aspect_ratio": "16:9",
                    "output_format": "png",
                    "output_quality": 90,
                },
            )

            if not output:
                logger.error(
                    "[GenerateOGImage] No output from Replicate",
                    blog_post_id=self.id,
                    project_id=self.project_id,
                )
                return False, f"Failed to generate image for blog post {self.id}"

            file_output = output[0] if isinstance(output, list) else output
            image_url = str(file_output)

            logger.info(
                "[GenerateOGImage] Image generated successfully",
                blog_post_id=self.id,
                project_id=self.project_id,
                image_url=image_url,
            )

            image_response = urlopen(image_url)
            image_content = ContentFile(image_response.read())

            filename = f"og-image-{self.id}.png"
            self.image.save(filename, image_content, save=True)

            logger.info(
                "[GenerateOGImage] Image saved to blog post",
                blog_post_id=self.id,
                project_id=self.project_id,
                saved_url=self.image.url,
            )

            return True, f"Successfully generated and saved OG image for blog post {self.id}"

        except replicate.exceptions.ReplicateError as replicate_error:
            logger.error(
                "[GenerateOGImage] Replicate API error",
                error=str(replicate_error),
                exc_info=True,
                blog_post_id=self.id,
                project_id=self.project_id,
            )
            return False, f"Replicate API error: {str(replicate_error)}"
        except Exception as error:
            logger.error(
                "[GenerateOGImage] Unexpected error during image generation",
                error=str(error),
                exc_info=True,
                blog_post_id=self.id,
                project_id=self.project_id,
            )
            return False, f"Unexpected error: {str(error)}"

    @staticmethod
    def _dedupe_pages_by_url(pages):
        unique_pages_by_url = {}
        for page in pages:
            if page.url not in unique_pages_by_url:
                unique_pages_by_url[page.url] = page
        return list(unique_pages_by_url.values())

    @staticmethod
    def _dedupe_external_pages_by_project(external_pages):
        """Keep at most one page per external project to diversify outbound links."""
        unique_pages_by_project = {}

        for page in external_pages:
            project_id = page.project_id
            if project_id not in unique_pages_by_project:
                unique_pages_by_project[project_id] = page

        return list(unique_pages_by_project.values())

    @staticmethod
    def _extract_domain(url: str) -> str:
        return (urlparse(url).netloc or "").lower()

    @staticmethod
    def _normalize_anchor(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @classmethod
    def _build_default_anchor_for_page(cls, page) -> str:
        return cls._normalize_anchor(page.title or page.description or page.url)

    def _evaluate_link_opportunity(self, *, page, link_source: str, now):
        """Apply hard safety controls to a candidate link page and return (allowed, details)."""
        relevance_threshold = 0.78 if link_source == "internal" else 0.74
        domain_window_days = 7
        max_domain_placements_per_window = 3
        max_source_target_domain_placements_per_window = 1
        max_identical_anchor_per_window = 1

        candidate_domain = self._extract_domain(page.url)
        source_domain = self._extract_domain(self.project.url)
        proposed_anchor = self._build_default_anchor_for_page(page)

        reasons = []
        flags = []
        relation = ""

        if link_source == "external":
            source_is_paid = bool(
                self.project.profile and self.project.profile.has_product_or_subscription
            )
            target_is_paid = bool(
                page.project
                and page.project.profile
                and page.project.profile.has_product_or_subscription
            )

            if not self.project.particiate_in_link_exchange:
                reasons.append("source_project_not_opted_in")
            if not page.project or not page.project.particiate_in_link_exchange:
                reasons.append("target_project_not_opted_in")

            # Eligibility policy:
            # - only paid projects are eligible to be promoted cross-project
            # - free projects must never be promoted into paid-project posts
            if not target_is_paid:
                if source_is_paid:
                    reasons.append("free_project_cannot_be_promoted_into_paid_post")
                else:
                    reasons.append("target_project_not_paid_for_promotion")

            relation = "nofollow"
            flags.extend(
                [
                    "external",
                    "nofollow_supported",
                    "source_project_paid" if source_is_paid else "source_project_free",
                    "target_project_paid" if target_is_paid else "target_project_free",
                ]
            )
        else:
            flags.append("internal")

        relevance_score = None
        if self.title_suggestion and page.embedding and self.title_suggestion.suggested_meta_description:
            query_embedding = get_jina_embedding(self.title_suggestion.suggested_meta_description)
            if query_embedding:
                dot_product = sum(a * b for a, b in zip(page.embedding, query_embedding))
                magnitude_page = sum(a * a for a in page.embedding) ** 0.5
                magnitude_query = sum(b * b for b in query_embedding) ** 0.5
                if magnitude_page > 0 and magnitude_query > 0:
                    relevance_score = dot_product / (magnitude_page * magnitude_query)
                    if relevance_score < relevance_threshold:
                        reasons.append("below_relevance_threshold")
                else:
                    reasons.append("missing_relevance_signal")
            else:
                reasons.append("missing_relevance_signal")
        else:
            reasons.append("missing_relevance_signal")

        window_start = now - timedelta(days=domain_window_days)
        recent_placements = LinkOpportunityAuditLog.objects.filter(
            phase=LinkOpportunityAuditLog.Phase.PLACEMENT,
            decision=LinkOpportunityAuditLog.Decision.PLACED,
            created_at__gte=window_start,
        )

        domain_placement_count = recent_placements.filter(candidate_domain=candidate_domain).count()
        if domain_placement_count >= max_domain_placements_per_window:
            reasons.append("domain_velocity_cap_exceeded")

        source_target_placement_count = recent_placements.filter(
            source_domain=source_domain,
            candidate_domain=candidate_domain,
        ).count()
        if source_target_placement_count >= max_source_target_domain_placements_per_window:
            reasons.append("source_target_velocity_cap_exceeded")

        identical_anchor_count = recent_placements.filter(
            candidate_domain=candidate_domain,
            final_anchor=proposed_anchor,
        ).count()
        if proposed_anchor and identical_anchor_count >= max_identical_anchor_per_window:
            reasons.append("anchor_diversity_cap_exceeded")

        eligibility_reasons = {
            "source_project_not_opted_in",
            "target_project_not_opted_in",
            "target_project_not_paid_for_promotion",
            "free_project_cannot_be_promoted_into_paid_post",
        }
        relevance_reasons = {
            "below_relevance_threshold",
            "missing_relevance_signal",
        }

        has_eligibility_rejection = any(reason in eligibility_reasons for reason in reasons)
        has_relevance_rejection = any(reason in relevance_reasons for reason in reasons)
        has_other_policy_rejection = any(
            reason not in eligibility_reasons and reason not in relevance_reasons for reason in reasons
        )

        if link_source == "external":
            flags.append("eligibility_failed" if has_eligibility_rejection else "eligibility_passed")
        else:
            flags.append("eligibility_not_applicable")

        flags.append("relevance_failed" if has_relevance_rejection else "relevance_passed")
        if has_other_policy_rejection:
            flags.append("policy_guardrail_failed")

        allowed = len(reasons) == 0

        details = {
            "phase": LinkOpportunityAuditLog.Phase.SUGGESTION,
            "decision": (
                LinkOpportunityAuditLog.Decision.ALLOWED
                if allowed
                else LinkOpportunityAuditLog.Decision.BLOCKED
            ),
            "source_project": self.project,
            "target_project": page.project if link_source == "external" else self.project,
            "generated_blog_post": self,
            "candidate_page": page,
            "candidate_url": page.url,
            "candidate_domain": candidate_domain,
            "source_domain": source_domain,
            "link_source": link_source,
            "relevance_score": relevance_score,
            "relevance_threshold": relevance_threshold,
            "proposed_anchor": proposed_anchor,
            "final_anchor": "",
            "relation": relation,
            "policy_flags": flags,
            "reasons": reasons,
        }
        return allowed, details

    def _evaluate_safe_link_opportunities(self, *, internal_pages, external_pages):
        now = timezone.now()
        safe_internal_pages = []
        safe_external_pages = []
        audit_logs = []

        for page in internal_pages:
            allowed, details = self._evaluate_link_opportunity(
                page=page,
                link_source="internal",
                now=now,
            )
            audit_logs.append(LinkOpportunityAuditLog(**details))
            if allowed:
                safe_internal_pages.append(page)

        for page in external_pages:
            allowed, details = self._evaluate_link_opportunity(
                page=page,
                link_source="external",
                now=now,
            )
            audit_logs.append(LinkOpportunityAuditLog(**details))
            if allowed:
                safe_external_pages.append(page)

        if audit_logs:
            LinkOpportunityAuditLog.objects.bulk_create(audit_logs)

        return safe_internal_pages, safe_external_pages

    def _record_link_placement_audit_logs(self, *, candidate_pages, content_with_links):
        links = re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", content_with_links)
        final_anchor_by_url = {url: self._normalize_anchor(anchor) for anchor, url in links}

        placement_logs = []
        for page in candidate_pages:
            final_anchor = final_anchor_by_url.get(page.url, "")
            placement_logs.append(
                LinkOpportunityAuditLog(
                    phase=LinkOpportunityAuditLog.Phase.PLACEMENT,
                    decision=(
                        LinkOpportunityAuditLog.Decision.PLACED
                        if final_anchor
                        else LinkOpportunityAuditLog.Decision.NOT_PLACED
                    ),
                    source_project=self.project,
                    target_project=page.project if page.project_id != self.project_id else self.project,
                    generated_blog_post=self,
                    candidate_page=page,
                    candidate_url=page.url,
                    candidate_domain=self._extract_domain(page.url),
                    source_domain=self._extract_domain(self.project.url),
                    link_source="external" if page.project_id != self.project_id else "internal",
                    relevance_score=None,
                    relevance_threshold=None,
                    proposed_anchor=self._build_default_anchor_for_page(page),
                    final_anchor=final_anchor,
                    relation="nofollow" if page.project_id != self.project_id else "",
                    policy_flags=["placed"] if final_anchor else ["not_placed"],
                    reasons=[] if final_anchor else ["agent_did_not_place_link"],
                )
            )

        if placement_logs:
            LinkOpportunityAuditLog.objects.bulk_create(placement_logs)

        now = timezone.now()
        for page in candidate_pages:
            if page.project_id == self.project_id:
                continue

            final_anchor = final_anchor_by_url.get(page.url, "")
            if not final_anchor:
                continue

            source_page_url = f"{self.project.url.rstrip('/')}/{self.slug.lstrip('/')}"
            ProjectEarnedLink.objects.update_or_create(
                source_project=self.project,
                target_project=page.project,
                source_page_url=source_page_url,
                target_page_url=page.url,
                defaults={
                    "source_generated_blog_post": self,
                    "source_page_title": self.title,
                    "target_page": page,
                    "last_anchor": final_anchor,
                    "last_seen_at": now,
                },
            )

    def _get_link_candidate_pages(self, max_pages=4, max_external_pages=3):
        manually_selected_project_pages = list(self.project.project_pages.filter(always_use=True))
        relevant_project_pages = list(
            get_relevant_pages_for_blog_post(
                self.project,
                self.title_suggestion.suggested_meta_description,
                max_pages=max_pages,
            )
        )

        internal_project_pages = self._dedupe_pages_by_url(
            manually_selected_project_pages + relevant_project_pages
        )

        external_project_pages = []
        if self.project.particiate_in_link_exchange:
            external_candidate_pool_size = max(max_external_pages * 4, max_external_pages)
            external_candidates = list(
                get_relevant_external_pages_for_blog_post(
                    meta_description=self.title_suggestion.suggested_meta_description,
                    exclude_project=self.project,
                    max_pages=external_candidate_pool_size,
                )
            )

            filtered_external_pages = [
                page
                for page in external_candidates
                if page.project and page.project.particiate_in_link_exchange
            ]
            external_project_pages = self._dedupe_external_pages_by_project(
                self._dedupe_pages_by_url(filtered_external_pages)
            )

        return internal_project_pages, external_project_pages, manually_selected_project_pages

    @staticmethod
    def _build_page_contexts(internal_pages, external_pages):
        contexts = [
            ProjectPageContext(
                url=page.url,
                title=page.title,
                description=page.description,
                summary=page.summary,
                link_source="internal",
            )
            for page in internal_pages
        ]
        contexts.extend(
            [
                ProjectPageContext(
                    url=page.url,
                    title=page.title,
                    description=page.description,
                    summary=page.summary,
                    link_source="external",
                )
                for page in external_pages
            ]
        )
        return contexts

    def insert_links_into_post(self, max_pages=4, max_external_pages=3):
        """
        Insert links from project pages into the blog post content organically.
        Uses PydanticAI to intelligently place links without modifying the content.

        Args:
            max_pages: Maximum number of internal project pages to use for linking (default: 4)
            max_external_pages: Maximum number of external project pages to use for linking (default: 3)

        Returns:
            str: The blog post content with links inserted
        """  # noqa: E501
        if not self.title_suggestion:
            logger.warning(
                "[InsertLinksIntoPost] No title suggestion found for blog post",
                blog_post_id=self.id,
                project_id=self.project_id,
            )
            return self.content

        (
            internal_project_pages,
            external_project_pages,
            manually_selected_project_pages,
        ) = self._get_link_candidate_pages(
            max_pages=max_pages,
            max_external_pages=max_external_pages,
        )

        if (
            external_project_pages
            and self.external_links_approval_status != self.ApprovalStatus.APPROVED
        ):
            blocked_logs = [
                LinkOpportunityAuditLog(
                    phase=LinkOpportunityAuditLog.Phase.SUGGESTION,
                    decision=LinkOpportunityAuditLog.Decision.BLOCKED,
                    source_project=self.project,
                    target_project=page.project,
                    generated_blog_post=self,
                    candidate_page=page,
                    candidate_url=page.url,
                    candidate_domain=self._extract_domain(page.url),
                    source_domain=self._extract_domain(self.project.url),
                    link_source="external",
                    proposed_anchor=self._build_default_anchor_for_page(page),
                    policy_flags=["requires_human_approval"],
                    reasons=["awaiting_external_links_approval"],
                )
                for page in external_project_pages
            ]
            LinkOpportunityAuditLog.objects.bulk_create(blocked_logs)
            self.create_workflow_audit_event(
                checkpoint="EXTERNAL_LINKS",
                event_type="ACTION_BLOCKED",
                actor_profile=self.project.profile,
                decision=self.external_links_approval_status,
                reason="awaiting_external_links_approval",
                metadata={"blocked_candidates": len(external_project_pages)},
            )
            external_project_pages = []

        safe_internal_pages, safe_external_pages = self._evaluate_safe_link_opportunities(
            internal_pages=internal_project_pages,
            external_pages=external_project_pages,
        )
        safe_external_pages = safe_external_pages[:max_external_pages]

        all_pages_to_link = safe_internal_pages + safe_external_pages

        if not all_pages_to_link:
            logger.info(
                "[InsertLinksIntoPost] No pages passed safety checks for link insertion",
                blog_post_id=self.id,
                project_id=self.project_id,
            )
            return self.content

        project_page_contexts = self._build_page_contexts(
            internal_pages=safe_internal_pages,
            external_pages=safe_external_pages,
        )

        # Extract URLs for logging
        urls_to_insert = [page.url for page in all_pages_to_link]
        internal_urls = [page.url for page in safe_internal_pages]
        external_urls = [page.url for page in safe_external_pages]

        link_insertion_context = LinkInsertionContext(
            blog_post_content=self.content,
            project_pages=project_page_contexts,
        )

        insert_links_agent = create_insert_links_agent()

        prompt = "Insert the provided project page links into the blog post content organically. Do not modify the existing content, only add links where appropriate."  # noqa: E501

        logger.info(
            "[InsertLinksIntoPost] Running link insertion agent",
            blog_post_id=self.id,
            project_id=self.project_id,
            num_total_pages=len(project_page_contexts),
            num_internal_pages=len(safe_internal_pages),
            num_external_pages=len(safe_external_pages),
            num_always_use_pages=len(manually_selected_project_pages),
            participate_in_link_exchange=self.project.particiate_in_link_exchange,
            urls_to_insert=urls_to_insert,
            internal_urls=internal_urls,
            external_urls=external_urls,
        )

        result = run_agent_synchronously(
            insert_links_agent,
            prompt,
            deps=link_insertion_context,
            function_name="insert_links_into_post",
            model_name="GeneratedBlogPost",
        )

        content_with_links = result.output

        self.content = content_with_links
        self.save(update_fields=["content"])
        self._record_link_placement_audit_logs(
            candidate_pages=all_pages_to_link,
            content_with_links=content_with_links,
        )

        logger.info(
            "[InsertLinksIntoPost] Links inserted successfully",
            blog_post_id=self.id,
            project_id=self.project_id,
        )

        return content_with_links


class ProjectIntegration(BaseModel):
    class Provider(models.TextChoices):
        GOOGLE_ANALYTICS = "google_analytics", "Google Analytics (GA4)"
        GOOGLE_SEARCH_CONSOLE = "google_search_console", "Google Search Console"
        PLAUSIBLE = "plausible", "Plausible"

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="integrations")
    provider = models.CharField(max_length=64, choices=Provider.choices)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DISCONNECTED,
    )

    external_account_email = models.EmailField(blank=True, default="")
    scope = models.TextField(blank=True, default="")

    access_token = models.TextField(blank=True, default="")
    refresh_token = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    plausible_api_key = models.CharField(max_length=255, blank=True, default="")
    plausible_site_id = models.CharField(max_length=255, blank=True, default="")
    plausible_base_url = models.URLField(max_length=255, blank=True, default="https://plausible.io")

    connected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("project", "provider")

    @property
    def is_connected(self):
        return self.status == self.Status.CONNECTED


class AnalyticsSourceSnapshot(BaseModel):
    class Provider(models.TextChoices):
        GA4 = "ga4", "GA4"
        GSC = "gsc", "Google Search Console"
        PLAUSIBLE = "plausible", "Plausible"

    class FetchStatus(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="analytics_source_snapshots")
    integration = models.ForeignKey(
        ProjectIntegration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_source_snapshots",
    )

    provider = models.CharField(max_length=32, choices=Provider.choices)
    source_account_ref = models.CharField(max_length=255)
    request_fingerprint = models.CharField(max_length=64)

    window_start_date = models.DateField()
    window_end_date = models.DateField()

    payload_json = models.JSONField(default=dict, blank=True)
    rows_count = models.IntegerField(default=0)
    fetched_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(max_length=32, choices=FetchStatus.choices, default=FetchStatus.SUCCESS)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["project", "provider", "window_start_date", "window_end_date", "-fetched_at"]
            ),
            models.Index(fields=["request_fingerprint"]),
        ]


class AnalyticsFactDaily(BaseModel):
    class Provider(models.TextChoices):
        GA4 = "ga4", "GA4"
        GSC = "gsc", "Google Search Console"
        PLAUSIBLE = "plausible", "Plausible"

    class DimensionScope(models.TextChoices):
        SITE = "site", "Site"
        PAGE = "page", "Page"
        QUERY = "query", "Query"
        PAGE_QUERY = "page_query", "Page + Query"
        COUNTRY = "country", "Country"
        DEVICE = "device", "Device"
        CHANNEL = "channel", "Channel"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="analytics_facts_daily")
    provider = models.CharField(max_length=32, choices=Provider.choices)
    metric_date = models.DateField()
    dimension_scope = models.CharField(max_length=32, choices=DimensionScope.choices)

    page_url = models.CharField(max_length=1024, blank=True, default="")
    page_url_key = models.CharField(max_length=64, blank=True, default="")
    search_query = models.CharField(max_length=512, blank=True, default="")
    search_query_key = models.CharField(max_length=64, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="")
    device_type = models.CharField(max_length=32, blank=True, default="")
    channel_group = models.CharField(max_length=64, blank=True, default="")
    dimension_fingerprint = models.CharField(max_length=64, default="")

    clicks = models.BigIntegerField(null=True, blank=True)
    impressions = models.BigIntegerField(null=True, blank=True)
    ctr = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    avg_position = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    sessions = models.BigIntegerField(null=True, blank=True)
    users = models.BigIntegerField(null=True, blank=True)
    engaged_sessions = models.BigIntegerField(null=True, blank=True)
    bounce_rate = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    conversions = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    conversion_rate = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    provider_payload_meta = models.JSONField(default=dict, blank=True)
    source_snapshot = models.ForeignKey(
        AnalyticsSourceSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="normalized_rows",
    )
    ingested_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "provider", "metric_date", "dimension_scope", "dimension_fingerprint"],
                name="analytics_fact_daily_unique_dimension",
            )
        ]
        indexes = [
            models.Index(fields=["project", "-metric_date"]),
            models.Index(fields=["project", "provider", "-metric_date"]),
            models.Index(fields=["project", "dimension_scope", "-metric_date"]),
            models.Index(fields=["page_url_key"]),
            models.Index(fields=["search_query_key"]),
        ]


class AnalyticsSyncCursor(BaseModel):
    class Provider(models.TextChoices):
        GOOGLE_ANALYTICS = ProjectIntegration.Provider.GOOGLE_ANALYTICS
        GOOGLE_SEARCH_CONSOLE = ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE
        PLAUSIBLE = ProjectIntegration.Provider.PLAUSIBLE

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="analytics_sync_cursors")
    provider = models.CharField(max_length=64, choices=Provider.choices)
    source_account_ref = models.CharField(max_length=255)

    last_successful_date = models.DateField(null=True, blank=True)
    backfill_start_date = models.DateField(null=True, blank=True)
    backfill_end_date = models.DateField(null=True, blank=True)

    last_run_started_at = models.DateTimeField(null=True, blank=True)
    last_run_finished_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=32, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "provider", "source_account_ref"],
                name="analytics_sync_cursor_unique_source",
            )
        ]


class ProjectPage(BaseModel):
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="project_pages"
    )

    url = models.URLField(max_length=200)
    source = models.CharField(
        max_length=20,
        choices=ProjectPageSource.choices,
        default=ProjectPageSource.AI,
        help_text="Source of the page: AI-discovered or from Sitemap",
    )

    # Content from Jina Reader
    date_scraped = models.DateTimeField(null=True, blank=True)
    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    markdown_content = models.TextField(blank=True, default="")

    # AI Content
    date_analyzed = models.DateTimeField(null=True, blank=True)
    type = models.CharField(max_length=255, choices=ProjectPageType.choices, blank=True, default="")
    type_ai_guess = models.CharField(max_length=255)
    summary = models.TextField(blank=True)

    # Embedding for semantic search
    embedding = VectorField(dimensions=1024, default=None, null=True, blank=True)

    # Link usage in blog posts
    always_use = models.BooleanField(
        default=False,
        help_text="When enabled, this page link will always be included in generated blog posts",
    )

    def __str__(self):
        return f"{self.project.name}: {self.title}"

    class Meta:
        unique_together = ("project", "url")
        indexes = [
            HnswIndex(
                name="projectpage_embedding_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def save(self, *args, **kwargs):
        """Override save to validate URL before saving."""
        self.clean()
        super().save(*args, **kwargs)

    def clean(self):
        """Validate that the URL is valid before saving."""
        from django.core.exceptions import ValidationError

        if not self.url:
            raise ValidationError("URL cannot be empty")

        if not isinstance(self.url, str):
            raise ValidationError("URL must be a string")

        if not self.url.startswith(("http://", "https://")):
            raise ValidationError(
                f"Invalid URL: {self.url}. URL must start with http:// or https://"
            )

        # Check if URL looks like an error message or invalid content
        if any(
            phrase in self.url.lower()
            for phrase in ["i need", "please provide", "error", "invalid", "missing"]
        ):
            raise ValidationError(f"Invalid URL content detected: {self.url}")

    @property
    def web_page_content(self):
        return WebPageContent(
            title=self.title,
            description=self.description,
            markdown_content=self.markdown_content,
        )

    def get_page_content(self):
        """
        Fetch page content using Jina Reader API and update the project.
        Returns the content if successful, raises ValueError otherwise.
        """
        title, description, markdown_content = get_markdown_content(self.url)

        if not title or not description or not markdown_content:
            return False

        self.date_scraped = timezone.now()
        self.title = title
        self.description = description
        self.markdown_content = markdown_content

        self.save(
            update_fields=[
                "date_scraped",
                "title",
                "description",
                "markdown_content",
            ]
        )

        return True

    def analyze_content(self):
        """
        Analyze the page content using Claude via PydanticAI and update project details.
        Should be called after get_page_content().
        """
        agent = create_summarize_page_agent()

        webpage_content = WebPageContent(
            title=self.title,
            description=self.description,
            markdown_content=self.markdown_content,
        )

        analysis_result = run_agent_synchronously(
            agent,
            "Please analyze this web page.",
            deps=webpage_content,
            function_name="analyze_content",
            model_name="ProjectPage",
        )

        self.date_analyzed = timezone.now()

        if self.type == "":
            self.type = analysis_result.output.type

        self.type_ai_guess = analysis_result.output.type_ai_guess
        self.summary = analysis_result.output.summary

        update_fields = [
            "date_analyzed",
            "type",
            "type_ai_guess",
            "summary",
        ]

        if self.title and self.description and self.summary:
            embedding_text = f"{self.title}\n\n{self.description}\n\n{self.summary}"
            embedding = get_jina_embedding(embedding_text)
            if embedding:
                self.embedding = embedding
                update_fields.append("embedding")
                logger.info(
                    "[ProjectPage.analyze_content] Successfully generated and saved embedding",
                    project_page_id=self.id,
                    project_id=self.project_id,
                )
            else:
                logger.warning(
                    "[ProjectPage.analyze_content] Failed to generate embedding",
                    project_page_id=self.id,
                    project_id=self.project_id,
                )
        else:
            logger.info(
                "[ProjectPage.analyze_content] Skipping embedding generation - missing required fields",  # noqa: E501
                project_page_id=self.id,
                project_id=self.project_id,
                has_title=bool(self.title),
                has_description=bool(self.description),
                has_summary=bool(self.summary),
            )

        self.save(update_fields=update_fields)

        return True


class Competitor(BaseModel):
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="competitors"
    )
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=200)
    description = models.TextField()

    date_scraped = models.DateTimeField(null=True, blank=True)
    homepage_title = models.CharField(max_length=500, blank=True, default="")
    homepage_description = models.TextField(blank=True, default="")
    markdown_content = models.TextField(blank=True)
    summary = models.TextField(blank=True)

    # Embedding for semantic search
    embedding = VectorField(dimensions=1024, default=None, null=True, blank=True)

    date_analyzed = models.DateTimeField(null=True, blank=True)
    # how does this competitor compare to the project?
    competitor_analysis = models.TextField(blank=True)
    key_differences = models.TextField(blank=True)
    strengths = models.TextField(blank=True)
    weaknesses = models.TextField(blank=True)
    opportunities = models.TextField(blank=True)
    threats = models.TextField(blank=True)
    key_features = models.TextField(blank=True)
    key_benefits = models.TextField(blank=True)
    key_drawbacks = models.TextField(blank=True)
    links = models.JSONField(default=list, blank=True, null=True)

    # VS comparison blog post content
    blog_post = models.TextField(blank=True, default="")
    blog_post_generation_status = models.CharField(
        max_length=20,
        choices=CompetitorPostGenerationStatus.choices,
        default=CompetitorPostGenerationStatus.IDLE,
    )
    blog_post_generation_started_at = models.DateTimeField(null=True, blank=True)
    blog_post_generation_completed_at = models.DateTimeField(null=True, blank=True)
    blog_post_generation_error = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            HnswIndex(
                name="competitor_embedding_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.name}"

    @property
    def is_blog_post_generation_in_progress(self) -> bool:
        return self.blog_post_generation_status == CompetitorPostGenerationStatus.PROCESSING

    @property
    def competitor_details(self):
        return CompetitorDetails(
            name=self.name,
            url=self.url,
            description=self.description,
        )

    def get_page_content(self):
        """
        Fetch page content using Jina Reader API and update the project.
        Returns the content if successful, raises ValueError otherwise.
        """
        homepage_title, homepage_description, markdown_content = get_markdown_content(self.url)

        if not homepage_title or not homepage_description or not markdown_content:
            return False

        self.date_scraped = timezone.now()
        self.homepage_title = homepage_title
        self.homepage_description = homepage_description
        self.markdown_content = markdown_content

        self.save(
            update_fields=[
                "date_scraped",
                "homepage_title",
                "homepage_description",
                "markdown_content",
            ]
        )

        return True

    def populate_name_description(self, model=None):
        agent = create_populate_competitor_details_agent(model)

        deps = WebPageContent(
            title=self.homepage_title,
            description=self.homepage_description,
            markdown_content=self.markdown_content,
        )
        result = run_agent_synchronously(
            agent,
            "Please analyze this competitor and extract the key information.",
            deps=deps,
            function_name="populate_name_description",
            model_name="Competitor",
        )

        self.name = result.output.name
        self.description = result.output.description

        update_fields = ["name", "description"]

        if self.name and self.description and self.summary:
            embedding_text = f"{self.name}\n\n{self.description}\n\n{self.summary}"
            embedding = get_jina_embedding(embedding_text)
            if embedding:
                self.embedding = embedding
                update_fields.append("embedding")
                logger.info(
                    "[Competitor.populate_name_description] Successfully generated and saved embedding",  # noqa: E501
                    competitor_id=self.id,
                    project_id=self.project_id,
                )
            else:
                logger.warning(
                    "[Competitor.populate_name_description] Failed to generate embedding",  # noqa: E501
                    competitor_id=self.id,
                    project_id=self.project_id,
                )
        else:
            logger.info(
                "[Competitor.populate_name_description] Skipping embedding generation - missing required fields",  # noqa: E501
                competitor_id=self.id,
                project_id=self.project_id,
                has_name=bool(self.name),
                has_description=bool(self.description),
                has_summary=bool(self.summary),
            )

        self.save(update_fields=update_fields)

        return True

    def analyze_competitor(self, model=None):
        agent = create_analyze_competitor_agent(model)

        deps = CompetitorAnalysisContext(
            project_details=self.project.project_details,
            competitor_details=self.competitor_details,
            competitor_homepage_content=self.markdown_content,
        )
        result = run_agent_synchronously(
            agent,
            "Please analyze this competitor and extract the key information.",
            deps=deps,
            function_name="analyze_competitor",
            model_name="Competitor",
        )

        self.competitor_analysis = result.output.competitor_analysis
        self.key_differences = result.output.key_differences
        self.strengths = result.output.strengths
        self.summary = result.output.summary
        self.weaknesses = result.output.weaknesses
        self.opportunities = result.output.opportunities
        self.threats = result.output.threats
        self.key_features = result.output.key_features
        self.key_benefits = result.output.key_benefits
        self.key_drawbacks = result.output.key_drawbacks
        self.links = result.output.links
        self.date_analyzed = timezone.now()

        if self.name and self.description and self.summary:
            embedding_text = f"{self.name}\n\n{self.description}\n\n{self.summary}"
            embedding = get_jina_embedding(embedding_text)
            if embedding:
                self.embedding = embedding
                logger.info(
                    "[Competitor.analyze_competitor] Successfully generated and saved embedding",
                    competitor_id=self.id,
                    project_id=self.project_id,
                )
            else:
                logger.warning(
                    "[Competitor.analyze_competitor] Failed to generate embedding",
                    competitor_id=self.id,
                    project_id=self.project_id,
                )
        else:
            logger.info(
                "[Competitor.analyze_competitor] Skipping embedding generation - missing required fields",  # noqa: E501
                competitor_id=self.id,
                project_id=self.project_id,
                has_name=bool(self.name),
                has_description=bool(self.description),
                has_summary=bool(self.summary),
            )

        self.save()

        return True

    def generate_vs_blog_post(self):
        """
        Generate comparison blog post content using Perplexity Sonar.
        This method uses Perplexity's web search capabilities to research both products.
        """
        from core.agents.schemas import CompetitorVsPostContext

        agent = create_competitor_vs_blog_post_agent()

        title = f"{self.project.name} vs. {self.name}: Which is Better?"

        # Get all analyzed project pages (from AI and sitemap sources)
        project_pages = [
            ProjectPageContext(
                url=page.url,
                title=page.title,
                description=page.description,
                summary=page.summary,
            )
            for page in self.project.project_pages.filter(date_analyzed__isnull=False)
        ]

        context = CompetitorVsPostContext(
            project_name=self.project.name,
            project_url=self.project.url,
            project_summary=self.project.summary,
            competitor_name=self.name,
            competitor_url=self.url,
            competitor_description=self.description,
            title=title,
            language=self.project.language,
            project_pages=project_pages,
        )

        prompt = "Write a comprehensive comparison blog post. Return ONLY the markdown content for the blog post, nothing else."  # noqa: E501

        result = run_agent_synchronously(
            agent,
            prompt,
            deps=context,
            function_name="generate_vs_blog_post",
            model_name="Competitor",
        )

        self.blog_post = result.output
        self.save(update_fields=["blog_post"])

        return self.blog_post


class AgentExecutionJob(BaseModel):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="agent_execution_jobs",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="agent_execution_jobs",
    )
    operation = models.CharField(
        max_length=64,
        choices=ExecutionJobOperation.choices,
    )
    status = models.CharField(
        max_length=32,
        choices=ExecutionJobStatus.choices,
        default=ExecutionJobStatus.QUEUED,
    )
    idempotency_key = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=128, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_reason = models.TextField(blank=True, default="")
    retry_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="retry_jobs",
    )
    queue_task_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = ("profile", "operation", "idempotency_key")
        indexes = [
            models.Index(fields=["profile", "status", "created_at"]),
            models.Index(fields=["project", "status", "created_at"]),
            models.Index(fields=["operation", "status", "created_at"]),
        ]

    def __str__(self):
        return f"Job {self.id} {self.operation} ({self.status})"


class ProjectEarnedLink(BaseModel):
    source_project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="outbound_earned_links",
    )
    target_project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="inbound_earned_links",
    )
    source_generated_blog_post = models.ForeignKey(
        GeneratedBlogPost,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="earned_links_created",
    )
    source_page_title = models.CharField(max_length=255, blank=True, default="")
    source_page_url = models.URLField(max_length=500, blank=True, default="")
    target_page = models.ForeignKey(
        ProjectPage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="earned_links_received",
    )
    target_page_url = models.URLField(max_length=500)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_anchor = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = (
            "source_project",
            "target_project",
            "source_page_url",
            "target_page_url",
        )
        indexes = [
            models.Index(fields=["target_project", "last_seen_at"]),
            models.Index(fields=["source_project", "last_seen_at"]),
            models.Index(fields=["target_page_url"]),
        ]


class LinkOpportunityAuditLog(BaseModel):
    class Phase(models.TextChoices):
        SUGGESTION = "SUGGESTION", "Suggestion"
        PLACEMENT = "PLACEMENT", "Placement"

    class Decision(models.TextChoices):
        ALLOWED = "ALLOWED", "Allowed"
        BLOCKED = "BLOCKED", "Blocked"
        PLACED = "PLACED", "Placed"
        NOT_PLACED = "NOT_PLACED", "Not Placed"

    phase = models.CharField(max_length=20, choices=Phase.choices)
    decision = models.CharField(max_length=20, choices=Decision.choices)
    source_project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="link_opportunity_logs_as_source",
    )
    target_project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="link_opportunity_logs_as_target",
    )
    generated_blog_post = models.ForeignKey(
        GeneratedBlogPost,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="link_opportunity_audit_logs",
    )
    candidate_page = models.ForeignKey(
        ProjectPage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="link_opportunity_audit_logs",
    )
    candidate_url = models.URLField(max_length=500)
    candidate_domain = models.CharField(max_length=255, blank=True, default="")
    source_domain = models.CharField(max_length=255, blank=True, default="")
    link_source = models.CharField(max_length=20, blank=True, default="")
    relevance_score = models.FloatField(null=True, blank=True)
    relevance_threshold = models.FloatField(null=True, blank=True)
    proposed_anchor = models.CharField(max_length=255, blank=True, default="")
    final_anchor = models.CharField(max_length=255, blank=True, default="")
    relation = models.CharField(max_length=32, blank=True, default="")
    policy_flags = models.JSONField(default=list, blank=True)
    reasons = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source_project", "phase", "created_at"]),
            models.Index(fields=["candidate_domain", "phase", "created_at"]),
            models.Index(fields=["decision", "phase", "created_at"]),
        ]


class BlogPostWorkflowAuditLog(BaseModel):
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="blog_post_workflow_audit_logs",
    )
    generated_blog_post = models.ForeignKey(
        GeneratedBlogPost,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_audit_logs",
    )
    checkpoint = models.CharField(max_length=32)
    event_type = models.CharField(max_length=64)
    actor_profile = models.ForeignKey(
        "Profile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="blog_post_workflow_events",
    )
    decision = models.CharField(max_length=32, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["generated_blog_post", "created_at"]),
            models.Index(fields=["project", "checkpoint", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("BlogPostWorkflowAuditLog is immutable and cannot be updated")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("BlogPostWorkflowAuditLog is immutable and cannot be deleted")


class OutcomeAttributionEvent(BaseModel):
    class Dimension(models.TextChoices):
        CONTENT = "content", "Content"
        DISTRIBUTION = "distribution", "Links / Distribution"
        TECHNICAL = "technical", "Technical Operations"

    profile = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outcome_attribution_events",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="outcome_attribution_events",
    )
    event_name = models.CharField(max_length=128)
    dimension = models.CharField(max_length=32, choices=Dimension.choices)
    outcome_metric = models.CharField(max_length=64)
    outcome_value = models.FloatField(default=1.0)
    occurred_at = models.DateTimeField(default=timezone.now)
    source_model = models.CharField(max_length=128)
    source_object_id = models.BigIntegerField(null=True, blank=True)
    event_fingerprint = models.CharField(max_length=64, unique=True)
    metadata = models.JSONField(default=dict, blank=True)
    schema_version = models.PositiveSmallIntegerField(default=1)

    class Meta:
        indexes = [
            models.Index(fields=["project", "occurred_at"]),
            models.Index(fields=["project", "dimension", "occurred_at"]),
            models.Index(fields=["project", "outcome_metric", "occurred_at"]),
            models.Index(fields=["event_name", "occurred_at"]),
        ]


class OutcomeAttributionRollup(BaseModel):
    class Granularity(models.TextChoices):
        DAY = "DAY", "Day"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="outcome_attribution_rollups",
    )
    window_start = models.DateField()
    granularity = models.CharField(max_length=8, choices=Granularity.choices, default=Granularity.DAY)
    dimension = models.CharField(max_length=32, choices=OutcomeAttributionEvent.Dimension.choices)
    outcome_metric = models.CharField(max_length=64)
    total_value = models.FloatField(default=0.0)
    event_count = models.PositiveIntegerField(default=0)
    last_aggregated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (
            "project",
            "window_start",
            "granularity",
            "dimension",
            "outcome_metric",
        )
        indexes = [
            models.Index(fields=["project", "window_start"]),
            models.Index(fields=["project", "window_start", "dimension"]),
            models.Index(fields=["project", "window_start", "outcome_metric"]),
        ]


class Keyword(BaseModel):
    keyword_text = models.CharField(max_length=255, help_text="The keyword string")
    volume = models.IntegerField(
        null=True, blank=True, help_text="The search volume of the keyword"
    )
    cpc_currency = models.CharField(
        max_length=10, blank=True, help_text="The currency of the CPC value"
    )
    cpc_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="The cost per click value"
    )
    competition = models.FloatField(
        null=True, blank=True, help_text="The competition metric of the keyword (0 to 1)"
    )
    country = models.CharField(
        max_length=10,
        blank=True,
        default="us",
        help_text="The country for which metrics were fetched",
    )
    data_source = models.CharField(
        max_length=3,
        choices=KeywordDataSource.choices,
        default=KeywordDataSource.GOOGLE_KEYWORD_PLANNER,
        blank=True,
        help_text="The data source for the keyword metrics",
    )
    last_fetched_at = models.DateTimeField(
        auto_now=True, help_text="Timestamp of when the data was last fetched"
    )
    got_related_keywords = models.BooleanField(default=False)
    got_people_also_search_for_keywords = models.BooleanField(default=False)

    class Meta:
        unique_together = ("keyword_text", "country", "data_source")
        verbose_name = "Keyword"
        verbose_name_plural = "Keywords"

    def __str__(self):
        return f"{self.keyword_text} ({self.country or 'global'} - {self.data_source or 'N/A'})"

    def fetch_and_update_metrics(self, currency="usd"):  # noqa: C901
        if not hasattr(settings, "KEYWORDS_EVERYWHERE_API_KEY"):
            logger.error("[KeywordFetch] KEYWORDS_EVERYWHERE_API_KEY not found in settings.")
            return False

        api_key = settings.KEYWORDS_EVERYWHERE_API_KEY
        api_url = "https://api.keywordseverywhere.com/v1/get_keyword_data"

        payload = {
            "kw[]": [self.keyword_text],
            "country": self.country,
            "currency": currency,
            "dataSource": self.data_source,
        }
        headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}

        try:
            response = requests.post(api_url, data=payload, headers=headers, timeout=30)
            response.raise_for_status()

            response_data = response.json()

            if (
                not response_data.get("data")
                or not isinstance(response_data["data"], list)
                or not response_data["data"][0]
            ):
                logger.warning(
                    "[KeywordFetch] No data found in API response for keyword.",
                    keyword_id=self.id,
                    keyword_text=self.keyword_text,
                    response_status=response.status_code,
                    response_content=response.text[:500],
                )
                return False

            keyword_api_data = response_data["data"][0]

            self.volume = keyword_api_data.get("vol")

            cpc_data = keyword_api_data.get("cpc", {})
            self.cpc_currency = cpc_data.get("currency", "")
            try:
                self.cpc_value = Decimal(str(cpc_data.get("value", "0.00")))
            except InvalidOperation:
                logger.warning(
                    "[KeywordFetch] Invalid CPC value for keyword.",
                    keyword_text=self.keyword_text,
                    keyword_id=self.id,
                    cpc_value_raw=cpc_data.get("value"),
                )
                self.cpc_value = Decimal("0.00")

            self.competition = keyword_api_data.get("competition")
            self.last_fetched_at = timezone.now()

            # Save keyword instance before handling trends to ensure FK exists
            self.save(
                update_fields=[
                    "volume",
                    "cpc_currency",
                    "cpc_value",
                    "competition",
                    "last_fetched_at",
                ]
            )

            trend_data = keyword_api_data.get("trend", [])
            if isinstance(trend_data, list):
                with transaction.atomic():
                    # Get a set of existing (month, year) tuples for efficient lookup
                    existing_trends_tuples = set(self.trends.values_list("month", "year"))

                    trends_to_create = []
                    for trend_item in trend_data:
                        if (
                            isinstance(trend_item, dict)
                            and "month" in trend_item
                            and "year" in trend_item
                            and "value" in trend_item
                        ):
                            month_str = str(trend_item["month"])
                            year_int = int(trend_item["year"])

                            # Check if this month/year combo already exists
                            if (month_str, year_int) not in existing_trends_tuples:
                                trends_to_create.append(
                                    KeywordTrend(
                                        keyword=self,
                                        month=month_str,
                                        year=year_int,
                                        value=int(trend_item["value"]),
                                    )
                                )
                    if trends_to_create:
                        KeywordTrend.objects.bulk_create(trends_to_create)

            return True

        except requests.exceptions.HTTPError as e:
            logger.error(
                "[KeywordFetch] HTTP error occurred.",
                keyword_id=self.id,
                keyword_text=self.keyword_text,
                error=str(e),
                exc_info=True,
                status_code=e.response.status_code if e.response else None,
                response_content=e.response.text[:500] if e.response else None,
            )
            # Specific handling for API error codes
            if e.response is not None:
                if e.response.status_code == 401:
                    logger.error("[KeywordFetch] API Key is missing or invalid.")
                elif e.response.status_code == 402:
                    logger.error("[KeywordFetch] Insufficient credits or invalid subscription.")
                elif e.response.status_code == 400:
                    logger.error("[KeywordFetch] Submitted request data is invalid.")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(
                "[KeywordFetch] Request exception occurred.",
                keyword_id=self.id,
                keyword_text=self.keyword_text,
                error=str(e),
                exc_info=True,
            )
            return False
        except Exception as e:
            logger.error(
                "[KeywordFetch] An unexpected error occurred.",
                keyword_id=self.id,
                keyword_text=self.keyword_text,
                error=str(e),
                exc_info=True,
            )
            return False


class ProjectKeyword(BaseModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="project_keywords")
    keyword = models.ForeignKey(Keyword, on_delete=models.CASCADE, related_name="keyword_projects")
    use = models.BooleanField(default=False)
    date_associated = models.DateTimeField(
        auto_now_add=True, help_text="When the keyword was associated with the project"
    )

    class Meta:
        unique_together = ("project", "keyword")
        verbose_name = "Project Keyword"
        verbose_name_plural = "Project Keywords"

    def __str__(self):
        return f"{self.project.name} - {self.keyword.keyword_text}"


class KeywordTrend(BaseModel):
    keyword = models.ForeignKey(Keyword, on_delete=models.CASCADE, related_name="trends")
    month = models.CharField(max_length=10, help_text="The month of this volume (e.g., May)")
    year = models.IntegerField(help_text="The year of this volume (e.g., 2019)")
    value = models.IntegerField(help_text="The search volume of the keyword for the given month")

    class Meta:
        unique_together = ("keyword", "month", "year")
        verbose_name = "Keyword Trend"
        verbose_name_plural = "Keyword Trends"
        ordering = ["keyword", "year", "month"]

    def __str__(self):
        return f"{self.keyword.keyword_text} - {self.month} {self.year}: {self.value}"


class Feedback(BaseModel):
    profile = models.ForeignKey(
        Profile, null=True, blank=True, on_delete=models.CASCADE, related_name="feedback"
    )
    feedback = models.TextField()
    page = models.CharField(max_length=255)
    date_submitted = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.profile.user.email}: {self.feedback}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)

        if is_new:
            from django.conf import settings
            from django.core.mail import send_mail

            subject = "New Feedback Submitted"
            message = f"""
                New feedback was submitted:
                User: {self.profile.user.email if self.profile else "Anonymous"}
                Feedback: {self.feedback}
                Page: {self.page}
            """
            from_email = settings.DEFAULT_FROM_EMAIL
            recipient_list = ["kireevr1996@gmail.com"]

            send_mail(subject, message, from_email, recipient_list, fail_silently=True)

            for recipient_email in recipient_list:
                async_task(
                    "core.tasks.track_email_sent",
                    email_address=recipient_email,
                    email_type=EmailType.FEEDBACK_NOTIFICATION,
                    profile=self.profile,
                    group="Track Email Sent",
                )


class ReferrerBanner(BaseModel):
    referrer = models.CharField(
        max_length=100,
        unique=True,
        help_text="The referrer code from URL parameter (e.g., 'producthunt' from ?ref=producthunt)",  # noqa: E501
    )
    referrer_printable_name = models.CharField(
        max_length=200,
        help_text="Human-readable name to display in banner (e.g., 'Product Hunt')",
    )
    expiry_date = models.DateTimeField(
        null=True, blank=True, help_text="When to stop showing this banner"
    )
    coupon_code = models.CharField(
        max_length=100, blank=True, help_text="Optional discount coupon code"
    )
    discount_amount = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0,
        help_text="Discount from 0.00 (0%) to 1.00 (100%)",
    )
    is_active = models.BooleanField(
        default=True, help_text="Manually enable/disable banner without deleting it"
    )
    background_color = models.CharField(
        max_length=100,
        default="bg-gradient-to-r from-red-500 to-red-600",
        help_text="Tailwind CSS background color classes (e.g., 'bg-gradient-to-r from-red-500 to-red-600' or 'bg-blue-600')",  # noqa: E501
    )
    text_color = models.CharField(
        max_length=50,
        default="text-white",
        help_text="Tailwind CSS text color class (e.g., 'text-white' or 'text-gray-900')",  # noqa: E501
    )

    def __str__(self):
        return f"{self.referrer_printable_name} ({self.referrer})"

    @property
    def is_expired(self):
        if self.expiry_date is None:
            return False
        return timezone.now() > self.expiry_date

    @property
    def should_display(self):
        return self.is_active and not self.is_expired

    @property
    def discount_percentage(self):
        return int(self.discount_amount * 100)


class EmailSent(BaseModel):
    email_address = models.EmailField(help_text="The recipient email address")
    email_type = models.CharField(
        max_length=50, choices=EmailType.choices, help_text="Type of email sent"
    )
    profile = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emails_sent",
        help_text="Associated user profile, if applicable",
    )

    class Meta:
        verbose_name = "Email Sent"
        verbose_name_plural = "Emails Sent"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email_type} to {self.email_address}"
