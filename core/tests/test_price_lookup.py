from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import views


def test_get_price_for_product_name_picks_canonical_yearly_amount_from_stripe_fallback():
    product_name = "Pro - Yearly"
    non_canonical = {
        "id": "price_1000",
        "product": SimpleNamespace(name=product_name),
        "unit_amount": 100000,
    }
    canonical = {
        "id": "price_990",
        "product": SimpleNamespace(name=product_name),
        "unit_amount": 99000,
    }

    with (
        patch.object(views.djstripe_models.Price.objects, "select_related") as mock_select_related,
        patch.object(views.stripe.Price, "list") as mock_stripe_list,
        patch.object(views.djstripe_models.Price, "sync_from_stripe_data") as mock_sync,
    ):
        mock_select_related.return_value.get.side_effect = (
            views.djstripe_models.Price.MultipleObjectsReturned
        )
        mock_stripe_list.return_value.auto_paging_iter.return_value = [non_canonical, canonical]
        mock_sync.return_value = "synced_canonical_price"

        result = views.get_price_for_product_name(product_name)

    assert result == "synced_canonical_price"
    mock_stripe_list.assert_called_once_with(active=True, expand=["data.product"], limit=100)
    mock_sync.assert_called_once_with(canonical)


def test_get_price_for_product_name_supports_explicit_env_override():
    product_name = "Pro - Monthly"
    explicit_price_id = "price_explicit_monthly"
    synced_price = SimpleNamespace(
        product=SimpleNamespace(name=product_name),
        active=True,
        unit_amount=9900,
    )

    with (
        patch.object(views, "settings") as mock_settings,
        patch.object(views.djstripe_models.Price.objects, "select_related") as mock_select_related,
        patch.object(views.stripe.Price, "retrieve") as mock_retrieve,
        patch.object(views.djstripe_models.Price, "sync_from_stripe_data") as mock_sync,
    ):
        mock_settings.STRIPE_LIVE_MODE = True
        mock_settings.STRIPE_PRICE_ID_PRO_MONTHLY = explicit_price_id
        mock_settings.STRIPE_PRICE_ID_PRO_YEARLY = ""

        mock_select_related.return_value.get.side_effect = views.djstripe_models.Price.DoesNotExist
        mock_retrieve.return_value = {"id": explicit_price_id}
        mock_sync.return_value = synced_price

        result = views.get_price_for_product_name(product_name)

    assert result == synced_price
    mock_retrieve.assert_called_once_with(explicit_price_id, expand=["product"])


def test_get_price_for_product_name_raises_when_price_is_missing():
    with (
        patch.object(views.djstripe_models.Price.objects, "select_related") as mock_select_related,
        patch.object(views.stripe.Price, "list") as mock_stripe_list,
    ):
        mock_select_related.return_value.get.side_effect = views.djstripe_models.Price.DoesNotExist
        mock_stripe_list.return_value.auto_paging_iter.return_value = []

        with pytest.raises(views.djstripe_models.Price.DoesNotExist):
            views.get_price_for_product_name("Pro - Yearly")
