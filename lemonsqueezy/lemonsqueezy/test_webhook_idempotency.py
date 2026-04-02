# Copyright (c) 2026, Ernesto Ruiz and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from lemonsqueezy.lemonsqueezy.api import (
    build_webhook_idempotency_key,
    get_existing_payment_entry,
    reserve_webhook_log,
)


class TestWebhookIdempotency(FrappeTestCase):
    def test_order_created_uses_order_id_as_idempotency_key(self):
        payload = {"meta": {"event_name": "order_created"}, "data": {"id": "12345"}}

        with patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.request",
            SimpleNamespace(headers={}),
        ):
            key, resource_id = build_webhook_idempotency_key(payload, b'{"id":"12345"}')

        self.assertEqual(key, "order_created:12345")
        self.assertEqual(resource_id, "12345")

    def test_non_order_event_falls_back_to_payload_hash_when_no_event_id(self):
        payload = {"meta": {"event_name": "subscription_updated"}, "data": {"id": "sub_123"}}
        raw_body = b'{"meta":{"event_name":"subscription_updated"},"data":{"id":"sub_123"}}'

        with patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.request",
            SimpleNamespace(headers={}),
        ):
            key, resource_id = build_webhook_idempotency_key(payload, raw_body)

        self.assertTrue(key.startswith("subscription_updated:sub_123:"))
        self.assertEqual(resource_id, "sub_123")

    def test_get_existing_payment_entry_returns_document(self):
        payment_entry = SimpleNamespace(name="ACC-PAY-0001")

        with patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.db.get_value",
            return_value=SimpleNamespace(name="ACC-PAY-0001", docstatus=1),
        ), patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.get_doc",
            return_value=payment_entry,
        ):
            result = get_existing_payment_entry("12345")

        self.assertIs(result, payment_entry)

    def test_reserve_webhook_log_reuses_failed_log_for_retry(self):
        log_doc = SimpleNamespace(
            status="Failed",
            event_name="order_created",
            resource_id="12345",
            payload="{}",
            error_message="boom",
            payment_entry="ACC-PAY-0001",
            save=Mock(),
        )

        with patch(
            "lemonsqueezy.lemonsqueezy.api.get_webhook_log_row",
            return_value=SimpleNamespace(name="WH-0001", status="Failed"),
        ), patch(
            "lemonsqueezy.lemonsqueezy.api.frappe.get_doc",
            return_value=log_doc,
        ):
            reserved_log, should_process = reserve_webhook_log(
                event_name="order_created",
                payload={"ok": True},
                idempotency_key="order_created:12345",
                resource_id="12345",
            )

        self.assertTrue(should_process)
        self.assertIs(reserved_log, log_doc)
        self.assertEqual(log_doc.status, "Processing")
        self.assertIsNone(log_doc.error_message)
        self.assertIsNone(log_doc.payment_entry)
        log_doc.save.assert_called_once()
