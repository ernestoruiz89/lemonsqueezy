# Copyright (c) 2026, Ernesto Ruiz and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from lemonsqueezy.lemonsqueezy.checkout import issue_checkout_token, validate_checkout_token
from lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings import (
    LemonSqueezySettings,
)


class TestCheckoutTokens(FrappeTestCase):
    def test_issue_and_validate_checkout_token(self):
        with patch(
            "lemonsqueezy.lemonsqueezy.checkout._get_checkout_token_secret",
            return_value=b"secret",
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.time.time",
            return_value=1_700_000_000,
        ):
            token = issue_checkout_token("PR-0001", "LemonSqueezy-Standard", expires_in_seconds=300)
            payload = validate_checkout_token(token)

        self.assertEqual(payload["payment_request"], "PR-0001")
        self.assertEqual(payload["settings"], "LemonSqueezy-Standard")

    def test_expired_checkout_token_is_rejected(self):
        with patch(
            "lemonsqueezy.lemonsqueezy.checkout._get_checkout_token_secret",
            return_value=b"secret",
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.time.time",
            side_effect=[1_700_000_000, 1_700_000_301],
        ):
            token = issue_checkout_token("PR-0001", "LemonSqueezy-Standard", expires_in_seconds=300)
            with self.assertRaises(frappe.ValidationError):
                validate_checkout_token(token)

    def test_tampered_checkout_token_is_rejected(self):
        with patch(
            "lemonsqueezy.lemonsqueezy.checkout._get_checkout_token_secret",
            return_value=b"secret",
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.time.time",
            return_value=1_700_000_000,
        ):
            token = issue_checkout_token("PR-0001", "LemonSqueezy-Standard", expires_in_seconds=300)
            tampered_token = token[:-1] + ("0" if token[-1] != "0" else "1")

            with self.assertRaises(frappe.ValidationError):
                validate_checkout_token(tampered_token)


class TestPaymentUrlIssuance(FrappeTestCase):
    def test_get_payment_url_issues_token_for_payment_request(self):
        settings = SimpleNamespace(name="LemonSqueezy-Standard")

        with patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.db.exists",
            return_value=True,
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.issue_checkout_token",
            return_value="signed-token",
        ) as issue_token, patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.get_checkout_redirect_url",
            return_value="https://example.com/checkout?token=signed-token",
        ):
            url = LemonSqueezySettings.get_payment_url(
                settings,
                reference_doctype="Payment Request",
                reference_docname="PR-0001",
            )

        issue_token.assert_called_once_with("PR-0001", "LemonSqueezy-Standard")
        self.assertEqual(url, "https://example.com/checkout?token=signed-token")

    def test_get_payment_url_rejects_non_payment_request(self):
        settings = SimpleNamespace(name="LemonSqueezy-Standard")

        url = LemonSqueezySettings.get_payment_url(
            settings,
            reference_doctype="Sales Invoice",
            reference_docname="SINV-0001",
        )

        self.assertIsNone(url)
