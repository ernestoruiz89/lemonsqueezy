# Copyright (c) 2026, Ernesto Ruiz and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from lemonsqueezy.lemonsqueezy.api import (
    ensure_customer_for_webhook,
    sync_direct_order_to_erpnext,
)


class _FakeCustomerDoc:
    def __init__(self):
        self.customer_name = None
        self.customer_type = None
        self.customer_group = None
        self.territory = None
        self.email_id = None
        self.name = "CUST-0001"

    def insert(self, ignore_permissions=False):
        return self


class TestDirectOrderSync(FrappeTestCase):
    def test_ensure_customer_for_webhook_creates_customer_with_defaults(self):
        fake_customer = _FakeCustomerDoc()
        settings = SimpleNamespace(default_customer_group="Retail", default_territory="All Territories")

        def fake_exists(doctype, name=None):
            if doctype == "DocType" and name in ("Customer", "Contact"):
                return True
            return False

        with patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.db.exists",
            side_effect=fake_exists,
        ), patch(
            "lemonsqueezy.lemonsqueezy.api._find_customer_by_email",
            return_value=None,
        ), patch(
            "lemonsqueezy.lemonsqueezy.api._ensure_contact_for_customer",
        ) as ensure_contact, patch(
            "lemonsqueezy.lemonsqueezy.api._get_customer_creation_defaults",
            return_value={"customer_group": "Retail", "territory": "All Territories"},
        ), patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.db.has_column",
            return_value=True,
        ), patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.new_doc",
            return_value=fake_customer,
        ):
            customer = ensure_customer_for_webhook(
                "buyer@example.com",
                settings,
                user_name="Buyer Example",
            )

        self.assertEqual(customer, "CUST-0001")
        self.assertEqual(fake_customer.customer_name, "Buyer Example")
        self.assertEqual(fake_customer.customer_group, "Retail")
        self.assertEqual(fake_customer.territory, "All Territories")
        self.assertEqual(fake_customer.email_id, "buyer@example.com")
        ensure_contact.assert_called_once_with("CUST-0001", "buyer@example.com", user_name="Buyer Example")

    def test_sync_direct_order_to_erpnext_creates_invoice_and_payment_links(self):
        order_context = {
            "order_id": "1001",
            "paid_amount": 25,
            "paid_currency": "USD",
            "user_email": "buyer@example.com",
            "user_name": "Buyer Example",
            "variant_id": "44565",
            "product_name": "Individual USD",
            "variant_name": "Monthly",
        }
        settings = SimpleNamespace()

        with patch(
            "lemonsqueezy.lemonsqueezy.api.ensure_customer_for_webhook",
            return_value="CUST-0001",
        ), patch(
            "lemonsqueezy.lemonsqueezy.api._resolve_variant_mapping",
            return_value={"item_code": "ITEM-SUB", "subscription_plan": "PLAN-USD"},
        ), patch(
            "lemonsqueezy.lemonsqueezy.api._get_direct_order_context",
            return_value={"company": "Sol Hogar", "payment_account": "LemonSqueeze USD - SH", "gateway_account": "LS - SH"},
        ), patch(
            "lemonsqueezy.lemonsqueezy.api.create_direct_sales_invoice",
            return_value="ACC-SINV-0001",
        ) as create_invoice, patch(
            "lemonsqueezy.lemonsqueezy.api.create_direct_payment_entry",
            return_value="ACC-PAY-0001",
        ) as create_payment, patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.set_user",
        ), patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.session",
            new=SimpleNamespace(user="Administrator"),
        ):
            result = sync_direct_order_to_erpnext(order_context, settings, existing_order=None)

        self.assertEqual(result["customer"], "CUST-0001")
        self.assertEqual(result["sales_invoice"], "ACC-SINV-0001")
        self.assertEqual(result["payment_entry_name"], "ACC-PAY-0001")
        create_invoice.assert_called_once()
        create_payment.assert_called_once_with(order_context, "ACC-SINV-0001", {"company": "Sol Hogar", "payment_account": "LemonSqueeze USD - SH", "gateway_account": "LS - SH"})
