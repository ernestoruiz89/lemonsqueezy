# Copyright (c) 2026, Ernesto Ruiz and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from lemonsqueezy.lemonsqueezy.checkout import (
    build_checkout_request,
    get_legacy_checkout_redirect_url,
    issue_checkout_token,
    validate_checkout_token,
)
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

    def test_legacy_checkout_link_upgrades_payment_request_to_signed_token(self):
        with patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.exists",
            return_value=True,
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.get_value",
            side_effect=[
                frappe._dict(
                    {
                        "reference_doctype": "Sales Invoice",
                        "reference_name": "SINV-0001",
                        "docstatus": 0,
                        "status": "Requested",
                    }
                ),
                "LemonSqueezy-Standard",
            ],
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.issue_checkout_token",
            return_value="signed-token",
        ) as issue_token, patch(
            "lemonsqueezy.lemonsqueezy.checkout.get_checkout_redirect_url",
            return_value="https://example.com/api/method/checkout?token=signed-token",
        ):
            url = get_legacy_checkout_redirect_url(
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_docname": "SINV-0001",
                    "order_id": "PR-0001",
                    "amount": 99,
                    "currency": "USD",
                }
            )

        issue_token.assert_called_once_with("PR-0001", "LemonSqueezy-Standard")
        self.assertEqual(url, "https://example.com/api/method/checkout?token=signed-token")

    def test_legacy_checkout_link_rejects_reference_mismatch(self):
        with patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.get_value",
            return_value=frappe._dict(
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_name": "SINV-0001",
                    "docstatus": 0,
                    "status": "Requested",
                }
            ),
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.exists",
            return_value=True,
        ):
            url = get_legacy_checkout_redirect_url(
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_docname": "SINV-0002",
                    "order_id": "PR-0001",
                }
            )

        self.assertIsNone(url)

    def test_legacy_checkout_link_rejects_non_payment_request_arguments(self):
        with patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.exists",
            return_value=False,
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.get_value",
            return_value=None,
        ):
            url = get_legacy_checkout_redirect_url(
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_docname": "SINV-0001",
                    "order_id": "SINV-0001",
                }
            )

        self.assertIsNone(url)

    def test_build_checkout_request_uses_invoice_item_amount_for_custom_price(self):
        payment_request = frappe._dict(
            {
                "name": "PR-0001",
                "status": "Requested",
                "reference_doctype": "Sales Invoice",
                "reference_name": "SINV-0001",
                "currency": "USD",
                "email_to": "customer@example.com",
                "party_name": "Customer",
                "grand_total": 120,
                "payment_amount": 120,
                "total_amount_to_pay": 120,
            }
        )
        invoice = frappe._dict(
            {
                "outstanding_amount": 120,
                "status": "Unpaid",
            }
        )
        settings = SimpleNamespace(verbose_logging=False)

        def fake_get_doc(doctype, name):
            if doctype == "Payment Request":
                return payment_request
            if doctype == "Sales Invoice":
                return invoice
            raise AssertionError(f"Unexpected doctype {doctype}")

        def fake_get_all(doctype, **kwargs):
            if doctype == "Sales Invoice Item":
                self.assertEqual(
                    kwargs["fields"],
                    ["item_code", "net_amount", "amount", "base_net_amount", "base_amount"],
                )
                return [
                    frappe._dict(
                        {
                            "item_code": "ITEM-001",
                            "net_amount": 99,
                            "amount": 108,
                            "base_net_amount": 99,
                            "base_amount": 108,
                        }
                    )
                ]
            return []

        def fake_get_value(doctype, name=None, fieldname=None, **kwargs):
            if doctype == "Item" and name == "ITEM-001" and fieldname == "lemonsqueezy_variant_id":
                return "VAR-001"
            return None

        with patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.exists",
            return_value=True,
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.has_column",
            side_effect=lambda doctype, fieldname: fieldname != "subscription_plan",
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.get_doc",
            side_effect=fake_get_doc,
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.get_all",
            side_effect=fake_get_all,
        ), patch(
            "lemonsqueezy.lemonsqueezy.checkout.frappe.db.get_value",
            side_effect=fake_get_value,
        ):
            checkout_request = build_checkout_request("PR-0001", settings)

        self.assertEqual(checkout_request["checkout_kwargs"]["variant_id"], "VAR-001")
        self.assertEqual(checkout_request["checkout_kwargs"]["amount"], 99)
