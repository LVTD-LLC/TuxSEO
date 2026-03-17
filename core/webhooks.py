from django_q.tasks import async_task
from djstripe.event_handlers import djstripe_receiver
from djstripe.models import Customer, Product, Subscription

from core.analytics import ANALYTICS_EVENTS
from core.models import Profile, ProfileStates
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


@djstripe_receiver("customer.subscription.created")
def handle_created_subscription(**kwargs):
    """
    Handle subscription creation webhook.
    Updates profile subscription, product, and customer, then tracks state change.
    """
    event = kwargs.get("event")
    if not event:
        logger.error("[SubscriptionCreated] No event provided")
        return

    try:
        event_data = event.data.get("object", {})
        subscription_id = event_data.get("id")
        customer_id = event_data.get("customer")

        # Get djstripe objects
        subscription = Subscription.objects.get(id=subscription_id)
        customer = Customer.objects.get(id=customer_id)

        # Get product from subscription items
        items_data = event_data.get("items", {}).get("data", [])
        product_id = items_data[0].get("price", {}).get("product")
        product = Product.objects.get(id=product_id)

        # Update profile
        profile = Profile.objects.get(customer=customer)
        profile.subscription = subscription
        profile.product = product
        profile.customer = customer
        profile.save(update_fields=["subscription", "product", "customer", "updated_at"])

        # Track state change
        profile.track_state_change(
            to_state=ProfileStates.SUBSCRIBED,
            metadata={
                "event": "subscription_created",
                "subscription_id": subscription_id,
                "product_id": product_id,
                "stripe_event_id": event.id,
            },
        )

        logger.info(
            "[SubscriptionCreated] Success",
            profile_id=profile.id,
            subscription_id=subscription_id,
            product_id=product_id,
        )

    except Exception as e:
        logger.error(
            "[SubscriptionCreated] Error",
            error=str(e),
            event_id=event.id,
            exc_info=True,
        )
        raise


@djstripe_receiver("customer.subscription.updated")
def handle_updated_subscription(**kwargs):
    """
    Handle subscription updates for cancellations and upgrades.
    Updates profile subscription/product and tracks state changes.
    """
    event = kwargs.get("event")
    if not event:
        logger.error("[SubscriptionUpdated] No event provided")
        return

    try:
        event_data = event.data.get("object", {})
        previous_attributes = event.data.get("previous_attributes", {})

        subscription_id = event_data.get("id")
        customer_id = event_data.get("customer")

        # Get djstripe objects
        subscription = Subscription.objects.get(id=subscription_id)
        customer = Customer.objects.get(id=customer_id)
        profile = Profile.objects.get(customer=customer)

        # Check if it's a cancellation
        cancel_at_period_end = event_data.get("cancel_at_period_end", False)
        cancellation_details = event_data.get("cancellation_details") or {}
        is_cancellation = (
            cancel_at_period_end and cancellation_details.get("reason") == "cancellation_requested"
        )

        # Check if it's an upgrade (plan changed)
        is_upgrade = "items" in previous_attributes or "plan" in previous_attributes

        # Update profile
        profile.subscription = subscription

        if is_upgrade:
            # Get new product from subscription items
            items_data = event_data.get("items", {}).get("data", [])
            product_id = items_data[0].get("price", {}).get("product")
            product = Product.objects.get(id=product_id)
            profile.product = product
            profile.save(update_fields=["subscription", "product", "updated_at"])

            # Track state change (remains SUBSCRIBED)
            profile.track_state_change(
                to_state=ProfileStates.SUBSCRIBED,
                metadata={
                    "event": "subscription_upgraded",
                    "subscription_id": subscription_id,
                    "product_id": product_id,
                    "stripe_event_id": event.id,
                },
            )
            logger.info(
                "[SubscriptionUpdated] Upgrade processed",
                profile_id=profile.id,
                product_id=product_id,
            )

            async_task(
                "core.tasks.track_event",
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.PLAN_UPGRADED,
                properties={
                    "plan": product.name,
                    "subscription_id": subscription_id,
                    "result_status": "succeeded",
                },
                source_function="webhooks.handle_updated_subscription",
                group="Track Event",
            )

        elif is_cancellation:
            profile.save(update_fields=["subscription", "updated_at"])

            # Track state change to CANCELLED
            profile.track_state_change(
                to_state=ProfileStates.CANCELLED,
                metadata={
                    "event": "subscription_cancelled",
                    "subscription_id": subscription_id,
                    "cancel_at": event_data.get("cancel_at"),
                    "current_period_end": event_data.get("current_period_end"),
                    "cancellation_reason": cancellation_details.get("reason"),
                    "stripe_event_id": event.id,
                },
            )
            logger.info(
                "[SubscriptionUpdated] Cancellation processed",
                profile_id=profile.id,
                cancel_at=event_data.get("cancel_at"),
            )

            plan_name = profile.product.name if profile.product else "free"
            async_task(
                "core.tasks.track_event",
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.PLAN_CANCELLED,
                properties={
                    "plan": plan_name,
                    "subscription_id": subscription_id,
                    "result_status": "succeeded",
                },
                source_function="webhooks.handle_updated_subscription",
                group="Track Event",
            )

        else:
            # Other updates - just update subscription reference
            profile.save(update_fields=["subscription", "updated_at"])
            logger.info(
                "[SubscriptionUpdated] Other update processed",
                profile_id=profile.id,
                changed_fields=list(previous_attributes.keys()),
            )

    except Exception as e:
        logger.error(
            "[SubscriptionUpdated] Error",
            error=str(e),
            event_id=event.id,
            exc_info=True,
        )
        raise


@djstripe_receiver("customer.subscription.deleted")
def handle_deleted_subscription(**kwargs):
    """
    Handle subscription deletion.
    Removes subscription/product references and transitions state to CHURNED.
    """
    event = kwargs.get("event")
    if not event:
        logger.error("[SubscriptionDeleted] No event provided")
        return

    try:
        event_data = event.data.get("object", {})
        customer_id = event_data.get("customer")
        subscription_id = event_data.get("id")

        # Get customer and profile
        customer = Customer.objects.get(id=customer_id)
        profile = Profile.objects.get(customer=customer)

        # Track state change to CHURNED
        cancellation_details = event_data.get("cancellation_details") or {}
        profile.track_state_change(
            to_state=ProfileStates.CHURNED,
            metadata={
                "event": "subscription_deleted",
                "subscription_id": subscription_id,
                "ended_at": event_data.get("ended_at"),
                "cancellation_reason": cancellation_details.get("reason"),
                "stripe_event_id": event.id,
            },
        )

        # Clear subscription and product references
        profile.subscription = None
        profile.product = None
        profile.save(update_fields=["subscription", "product", "updated_at"])

        logger.info(
            "[SubscriptionDeleted] Success",
            profile_id=profile.id,
            subscription_id=subscription_id,
        )

    except Exception as e:
        logger.error(
            "[SubscriptionDeleted] Error",
            error=str(e),
            event_id=event.id,
            exc_info=True,
        )
        raise


@djstripe_receiver("checkout.session.completed")
def handle_checkout_completed(**kwargs):
    """
    Handle checkout.session.completed webhook.

    This is handled by other webhooks:
    - Subscriptions: handled by customer.subscription.created
    - Payments: not part of our current flow
    - Setup: doesn't require state changes

    We log this for tracking purposes only.
    """
    event = kwargs.get("event")
    if not event:
        logger.error("[CheckoutCompleted] No event provided")
        return

    try:
        event_data = event.data.get("object", {})
        customer_id = event_data.get("customer")
        profile = Profile.objects.filter(customer__id=customer_id).first()

        if profile:
            async_task(
                "core.tasks.track_event",
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.CHECKOUT_SUCCEEDED,
                properties={
                    "checkout_id": event_data.get("id"),
                    "mode": event_data.get("mode"),
                    "payment_status": event_data.get("payment_status"),
                    "subscription_id": event_data.get("subscription"),
                },
                source_function="webhooks.handle_checkout_completed",
                group="Track Event",
            )

        logger.info(
            "[CheckoutCompleted] Checkout completed",
            checkout_id=event_data.get("id"),
            customer_id=customer_id,
            mode=event_data.get("mode"),
            payment_status=event_data.get("payment_status"),
            subscription_id=event_data.get("subscription"),
        )

    except Exception as e:
        logger.error(
            "[CheckoutCompleted] Error",
            error=str(e),
            event_id=event.id,
            exc_info=True,
        )
