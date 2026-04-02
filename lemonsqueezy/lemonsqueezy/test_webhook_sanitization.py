# Copyright (c) 2026, Ernesto Ruiz and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

from lemonsqueezy.lemonsqueezy.api import sanitize_payload


class TestWebhookSanitization(FrappeTestCase):
    def test_sanitize_payload_redacts_nested_sensitive_fields(self):
        payload = {
            "meta": {"event_name": "order_created"},
            "data": {
                "attributes": {
                    "user_email": "customer@example.com",
                    "ip_address": "203.0.113.10",
                    "billing_address": {"country": "US"},
                    "items": [
                        {
                            "customer_email": "other@example.com",
                            "user_agent": "Mozilla/5.0",
                        }
                    ],
                }
            },
        }

        sanitized = sanitize_payload(payload)

        self.assertEqual(
            sanitized["data"]["attributes"]["user_email"],
            "[REDACTED]",
        )
        self.assertEqual(
            sanitized["data"]["attributes"]["ip_address"],
            "[REDACTED]",
        )
        self.assertEqual(
            sanitized["data"]["attributes"]["billing_address"],
            "[REDACTED]",
        )
        self.assertEqual(
            sanitized["data"]["attributes"]["items"][0]["customer_email"],
            "[REDACTED]",
        )
        self.assertEqual(
            sanitized["data"]["attributes"]["items"][0]["user_agent"],
            "[REDACTED]",
        )
        self.assertEqual(
            payload["data"]["attributes"]["user_email"],
            "customer@example.com",
        )
