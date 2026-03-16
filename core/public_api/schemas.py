from ninja import Schema
from pydantic import Field


class PublicAPIErrorOut(Schema):
    status: str = "error"
    code: str | None = None
    message: str
    upgrade_url: str | None = None


class PublicAccountOut(Schema):
    account_id: int
    email: str
    product_name: str
    is_on_pro_plan: bool
    project_limit: int | None = None
    active_project_count: int


class PublicProjectIn(Schema):
    url: str
    source: str = "public_api"


class PublicProjectOut(Schema):
    project_id: int
    name: str
    type: str
    url: str
    summary: str
    blog_theme: str = ""
    founders: str = ""
    key_features: str = ""
    target_audience_summary: str = ""
    pain_points: str = ""
    product_usage: str = ""
    links: str = ""
    language: str = ""
    location: str = ""


class PublicProjectCreateOut(Schema):
    status: str
    message: str = ""
    project: PublicProjectOut | None = None


class PublicProjectGetOut(Schema):
    status: str
    message: str = ""
    project: PublicProjectOut | None = None


class PublicProjectUpdateIn(Schema):
    name: str | None = None
    summary: str | None = None
    blog_theme: str | None = None
    founders: str | None = None
    key_features: str | None = None
    target_audience_summary: str | None = None
    pain_points: str | None = None
    product_usage: str | None = None
    links: str | None = None
    language: str | None = None
    location: str | None = None


class PublicProjectUpdateOut(Schema):
    status: str
    message: str = ""
    project: PublicProjectOut | None = None


class PublicContentAutomationIn(Schema):
    endpoint_url: str
    request_body_json: dict = Field(default_factory=dict)
    request_headers_json: dict = Field(default_factory=dict)
    posts_per_month: int = Field(default=1, gt=0)
    enable_automatic_post_submission: bool = True


class PublicContentAutomationOut(Schema):
    status: str
    message: str
    project_id: int
    content_automation_id: int
    enable_automatic_post_submission: bool


class PublicTitleSuggestionOut(Schema):
    id: int
    title: str
    category: str = ""
    description: str = ""
    target_keywords: list[str] = []
    suggested_meta_description: str = ""
    content_type: str
    status: str


class PublicPaginationOut(Schema):
    page: int
    page_size: int
    total: int


class PublicProjectListOut(Schema):
    status: str
    projects: list[PublicProjectOut] = []
    pagination: PublicPaginationOut


class PublicTitleSuggestionListOut(Schema):
    status: str
    suggestions: list[PublicTitleSuggestionOut] = []
    pagination: PublicPaginationOut


class PublicTitleSuggestionGetOut(Schema):
    status: str
    suggestion: PublicTitleSuggestionOut | None = None


class PublicTitleSuggestionCreateIn(Schema):
    count: int = Field(default=3, gt=0)
    content_type: str = "SHARING"
    seed_guidance: str = ""


class PublicTitleSuggestionCreateOut(Schema):
    status: str
    count: int
    suggestions: list[PublicTitleSuggestionOut] = []


class PublicKeywordTrendOut(Schema):
    value: int
    month: str
    year: int


class PublicKeywordOut(Schema):
    id: int
    keyword_text: str
    volume: int | None = None
    cpc_currency: str | None = None
    cpc_value: float | None = None
    competition: float | None = None
    country: str | None = None
    data_source: str | None = None
    last_fetched_at: str | None = None
    trend_data: list[PublicKeywordTrendOut] = []
    project_keyword_id: int
    in_use: bool


class PublicKeywordListOut(Schema):
    status: str
    keywords: list[PublicKeywordOut] = []
    pagination: PublicPaginationOut


class PublicKeywordGetOut(Schema):
    status: str
    keyword: PublicKeywordOut | None = None


class PublicKeywordCreateIn(Schema):
    keyword_text: str


class PublicKeywordCreateOut(Schema):
    status: str
    message: str = ""
    keyword: PublicKeywordOut | None = None


class PublicCompetitorOut(Schema):
    id: int
    project_id: int
    name: str
    url: str
    description: str
    summary: str = ""
    homepage_title: str = ""
    homepage_description: str = ""
    date_scraped: str | None = None
    date_analyzed: str | None = None
    blog_post_generation_status: str
    blog_post_generation_started_at: str | None = None
    blog_post_generation_completed_at: str | None = None
    blog_post_generation_error: str = ""
    created_at: str
    updated_at: str


class PublicCompetitorListOut(Schema):
    status: str
    competitors: list[PublicCompetitorOut] = []
    pagination: PublicPaginationOut


class PublicCompetitorGetOut(Schema):
    status: str
    competitor: PublicCompetitorOut | None = None


class PublicCompetitorCreateIn(Schema):
    url: str
    name: str = ""
    description: str = ""
    analyze_now: bool = True


class PublicCompetitorCreateOut(Schema):
    status: str
    message: str = ""
    competitor: PublicCompetitorOut | None = None


class PublicProjectPageOut(Schema):
    id: int
    project_id: int
    url: str
    source: str
    always_use: bool
    type: str = ""
    type_ai_guess: str = ""
    title: str = ""
    description: str = ""
    summary: str = ""
    date_scraped: str | None = None
    date_analyzed: str | None = None
    created_at: str
    updated_at: str


class PublicProjectPageListOut(Schema):
    status: str
    pages: list[PublicProjectPageOut] = []
    pagination: PublicPaginationOut


class PublicProjectPageGetOut(Schema):
    status: str
    page: PublicProjectPageOut | None = None


class PublicProjectPageCreateIn(Schema):
    url: str
    analyze_now: bool = True


class PublicProjectPageCreateOut(Schema):
    status: str
    message: str = ""
    page: PublicProjectPageOut | None = None


class PublicLinkAuditLogOut(Schema):
    id: int
    phase: str
    decision: str
    candidate_url: str
    candidate_domain: str = ""
    link_source: str = ""
    relevance_score: float | None = None
    relevance_threshold: float | None = None
    proposed_anchor: str = ""
    final_anchor: str = ""
    relation: str = ""
    policy_flags: list[str] = []
    reasons: list[str] = []
    created_at: str


class PublicWorkflowAuditLogOut(Schema):
    id: int
    checkpoint: str
    event_type: str
    decision: str = ""
    reason: str = ""
    actor_profile_id: int | None = None
    metadata: dict = {}
    created_at: str


class PublicBlogPostOut(Schema):
    id: int
    title: str
    slug: str
    description: str = ""
    tags: str = ""
    posted: bool
    date_posted: str | None = None
    title_suggestion_id: int | None = None
    content: str | None = None
    publish_approval_status: str = ""
    external_links_approval_status: str = ""
    publish_review_reason: str = ""
    external_links_review_reason: str = ""
    link_audit_logs: list[PublicLinkAuditLogOut] = []
    workflow_audit_logs: list[PublicWorkflowAuditLogOut] = []


class PublicBlogPostListOut(Schema):
    status: str
    posts: list[PublicBlogPostOut] = []
    pagination: PublicPaginationOut


class PublicBlogPostGetOut(Schema):
    status: str
    post: PublicBlogPostOut | None = None


class PublicBlogPostGenerateIn(Schema):
    title_suggestion_id: int


class PublicBlogPostGenerateOut(Schema):
    status: str
    message: str = ""
    post: PublicBlogPostOut | None = None


class PublicBlogPostPublishOut(Schema):
    status: str
    message: str = ""
    post: PublicBlogPostOut | None = None


class PublicBlogPostApprovalReviewIn(Schema):
    checkpoint: str
    decision: str
    reason: str = ""


class PublicBlogPostApprovalReviewOut(Schema):
    status: str
    message: str = ""
    post: PublicBlogPostOut | None = None


class PublicExecutionJobCreateIn(Schema):
    operation: str
    title_suggestion_id: int | None = None


class PublicExecutionJobOut(Schema):
    id: int
    project_id: int
    operation: str
    status: str
    idempotency_key: str
    payload: dict = {}
    result: dict = {}
    error_code: str = ""
    error_message: str = ""
    queued_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    canceled_at: str | None = None
    created_at: str
    updated_at: str


class PublicExecutionJobCreateOut(Schema):
    status: str
    message: str = ""
    created: bool = True
    job: PublicExecutionJobOut | None = None


class PublicExecutionJobGetOut(Schema):
    status: str
    job: PublicExecutionJobOut | None = None


class PublicExecutionJobListOut(Schema):
    status: str
    jobs: list[PublicExecutionJobOut] = []
    pagination: PublicPaginationOut


class PublicExecutionJobActionOut(Schema):
    status: str
    message: str = ""
    job: PublicExecutionJobOut | None = None
