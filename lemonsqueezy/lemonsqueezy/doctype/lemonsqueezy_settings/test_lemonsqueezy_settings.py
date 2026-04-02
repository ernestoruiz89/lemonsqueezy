# Copyright (c) 2026, Ernesto Ruiz and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings import (
    _can_access_customer_portal,
)


class TestLemonSqueezySettings(FrappeTestCase):
    def test_system_manager_can_access_any_subscription(self):
        subscription = SimpleNamespace(customer=None, customer_email=None)

        with patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.get_roles",
            return_value=["System Manager"],
        ):
            self.assertTrue(_can_access_customer_portal(subscription, user="admin@example.com"))

    def test_subscription_owner_can_access_by_email(self):
        subscription = SimpleNamespace(customer=None, customer_email="owner@example.com")

        with patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.get_roles",
            return_value=["Website User"],
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.has_permission",
            return_value=False,
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.db.get_value",
            return_value="owner@example.com",
        ):
            self.assertTrue(_can_access_customer_portal(subscription, user="owner@example.com"))

    def test_linked_customer_can_access_subscription(self):
        subscription = SimpleNamespace(customer="CUST-0001", customer_email=None)

        with patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.get_roles",
            return_value=["Website User"],
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.has_permission",
            return_value=False,
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.db.get_value",
            return_value="portal@example.com",
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings._get_customer_names_for_user",
            return_value={"CUST-0001"},
        ):
            self.assertTrue(_can_access_customer_portal(subscription, user="portal@example.com"))

    def test_unrelated_user_is_denied(self):
        subscription = SimpleNamespace(customer="CUST-0001", customer_email="owner@example.com")

        with patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.get_roles",
            return_value=["Website User"],
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.has_permission",
            return_value=False,
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.frappe.db.get_value",
            return_value="intruder@example.com",
        ), patch(
            "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings._get_customer_names_for_user",
            return_value=set(),
        ):
            self.assertFalse(_can_access_customer_portal(subscription, user="intruder@example.com"))
