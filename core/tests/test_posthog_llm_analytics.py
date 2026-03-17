from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.utils import LLM_ANALYTICS_EVENT, run_agent_synchronously


@contextmanager
def _fake_capture_run_messages():
    yield []


class _FakeUsage:
    input_tokens = 12
    output_tokens = 34
    total_tokens = 46


class _ZeroPreservingUsage:
    input_tokens = 0
    request_tokens = 99
    output_tokens = 0
    response_tokens = 77
    total_tokens = 0


class _FakeResult:
    output = "Generated output text"

    def __init__(self, usage_cls=_FakeUsage):
        self.usage_cls = usage_cls

    def usage(self):
        return self.usage_cls()


class _FakeAgent:
    model = SimpleNamespace(model_name="google-gla:gemini-2.5-flash")

    def __init__(self, usage_cls=_FakeUsage):
        self.usage_cls = usage_cls

    async def run(self, input_string, deps=None):
        return _FakeResult(usage_cls=self.usage_cls)


class _FailingAgent:
    model = SimpleNamespace(model_name="google-gla:gemini-2.5-flash")

    async def run(self, input_string, deps=None):
        raise RuntimeError("upstream timeout")


@patch("core.utils.capture_run_messages", _fake_capture_run_messages)
def test_run_agent_synchronously_emits_posthog_llm_generation_event(settings):
    settings.POSTHOG_API_KEY = "phc_test"

    deps = SimpleNamespace(
        id=42,
        user=SimpleNamespace(id=7, email="owner@example.com"),
        project=SimpleNamespace(id=4242),
    )

    with patch("core.utils.posthog.capture") as capture_mock:
        result = run_agent_synchronously(
            _FakeAgent(),
            "generate title suggestions",
            deps=deps,
            function_name="generate_title_suggestions",
            model_name="Project",
        )

    assert result.output == "Generated output text"
    capture_mock.assert_called_once()

    assert capture_mock.call_args.args[0] == "owner@example.com"

    call_args = capture_mock.call_args.kwargs
    assert call_args["event"] == LLM_ANALYTICS_EVENT

    properties = call_args["properties"]
    assert properties["feature_path"] == "Project.generate_title_suggestions"
    assert properties["result_status"] == "succeeded"
    assert properties["$ai_input_tokens"] == 12
    assert properties["$ai_output_tokens"] == 34
    assert properties["$ai_total_tokens"] == 46


@patch("core.utils.capture_run_messages", _fake_capture_run_messages)
def test_run_agent_synchronously_preserves_zero_token_metrics(settings):
    settings.POSTHOG_API_KEY = "phc_test"

    with patch("core.utils.posthog.capture") as capture_mock:
        run_agent_synchronously(_FakeAgent(usage_cls=_ZeroPreservingUsage), "generate")

    properties = capture_mock.call_args.kwargs["properties"]
    assert properties["$ai_input_tokens"] == 0
    assert properties["$ai_output_tokens"] == 0
    assert properties["$ai_total_tokens"] == 0


@patch("core.utils.capture_run_messages", _fake_capture_run_messages)
def test_run_agent_synchronously_emits_failed_llm_generation_event(settings):
    settings.POSTHOG_API_KEY = "phc_test"

    with patch("core.utils.posthog.capture") as capture_mock:
        with pytest.raises(RuntimeError, match="upstream timeout"):
            run_agent_synchronously(
                _FailingAgent(),
                "generate post",
                function_name="generate_blog_post_content",
                model_name="GeneratedBlogPost",
            )

    capture_mock.assert_called_once()
    properties = capture_mock.call_args.kwargs["properties"]

    assert properties["feature_path"] == "GeneratedBlogPost.generate_blog_post_content"
    assert properties["result_status"] == "failed"
    assert properties["error_type"] == "RuntimeError"
    assert "upstream timeout" in properties["error_message"]


@patch("core.utils.capture_run_messages", _fake_capture_run_messages)
def test_run_agent_synchronously_skips_llm_generation_event_without_posthog_key(settings):
    settings.POSTHOG_API_KEY = ""

    with patch("core.utils.posthog.capture") as capture_mock:
        result = run_agent_synchronously(_FakeAgent(), "hello world")

    assert result.output == "Generated output text"
    capture_mock.assert_not_called()
