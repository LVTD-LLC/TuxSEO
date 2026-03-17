from types import SimpleNamespace
from unittest.mock import patch

from core.analytics import ANALYTICS_EVENTS
from core.webhooks import handle_created_subscription


def test_subscription_created_webhook_emits_server_side_revenue_events():
    fake_event = SimpleNamespace(
        id="evt_sub_created_123",
        data={
            "object": {
                "id": "sub_123",
                "customer": "cus_123",
                "items": {"data": [{"price": {"product": "prod_123"}}]},
            }
        },
    )

    fake_profile = SimpleNamespace(
        id=42,
        subscription=None,
        product=None,
        customer=None,
        save=lambda **kwargs: None,
        track_state_change=lambda **kwargs: None,
    )

    with patch("core.webhooks.Subscription.objects.get", return_value=SimpleNamespace(id="sub_123")):
        with patch("core.webhooks.Customer.objects.get", return_value=SimpleNamespace(id="cus_123")):
            with patch("core.webhooks.Product.objects.get", return_value=SimpleNamespace(id="prod_123", name="Pro")):
                with patch("core.webhooks.Profile.objects.get", return_value=fake_profile):
                    with patch("core.webhooks.async_task") as async_task_mock:
                        handle_created_subscription(event=fake_event)

    emitted_event_names = [call.kwargs.get("event_name") for call in async_task_mock.call_args_list]

    assert ANALYTICS_EVENTS.SUBSCRIPTION_CREATED in emitted_event_names
    assert ANALYTICS_EVENTS.SUBSCRIPTION_STARTED in emitted_event_names
    assert ANALYTICS_EVENTS.PAID_CONVERSION in emitted_event_names
