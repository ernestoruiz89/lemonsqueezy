import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import flt

CHECKOUT_TOKEN_VERSION = 1
CHECKOUT_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def _encode_token_payload(payload_bytes):
    return base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")


def _decode_token_payload(encoded_payload):
    padding = "=" * (-len(encoded_payload) % 4)
    return base64.urlsafe_b64decode(encoded_payload + padding)


def _get_checkout_token_secret():
    secret = (
        frappe.local.conf.get("encryption_key")
        or frappe.local.conf.get("secret")
        or frappe.local.site
    )
    if not secret:
        frappe.throw(_("Checkout token secret is not configured."))

    return secret.encode() if isinstance(secret, str) else secret


def issue_checkout_token(payment_request_name, settings_name, expires_in_seconds=CHECKOUT_TOKEN_TTL_SECONDS):
    payload = {
        "v": CHECKOUT_TOKEN_VERSION,
        "payment_request": str(payment_request_name),
        "settings": str(settings_name),
        "exp": int(time.time()) + int(expires_in_seconds),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(_get_checkout_token_secret(), payload_bytes, hashlib.sha256).hexdigest()
    return f"{_encode_token_payload(payload_bytes)}.{signature}"


def validate_checkout_token(token):
    token = str(token or "").strip()
    if not token or "." not in token:
        frappe.throw(_("Invalid or expired checkout token."))

    encoded_payload, provided_signature = token.split(".", 1)

    try:
        payload_bytes = _decode_token_payload(encoded_payload)
        payload = json.loads(payload_bytes)
    except Exception:
        frappe.throw(_("Invalid or expired checkout token."))

    expected_signature = hmac.new(
        _get_checkout_token_secret(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, provided_signature):
        frappe.throw(_("Invalid or expired checkout token."))

    if payload.get("v") != CHECKOUT_TOKEN_VERSION:
        frappe.throw(_("Invalid or expired checkout token."))

    if int(payload.get("exp") or 0) < int(time.time()):
        frappe.throw(_("Invalid or expired checkout token."))

    if not payload.get("payment_request") or not payload.get("settings"):
        frappe.throw(_("Invalid or expired checkout token."))

    return payload


def get_checkout_redirect_url(token):
    return frappe.utils.get_url(
        "/api/method/lemonsqueezy.lemonsqueezy.api.lemonsqueezy_checkout?{0}".format(
            urlencode({"token": token})
        )
    )


def get_legacy_checkout_redirect_url(params):
    """
    Convert legacy public checkout links into the new signed-token flow.
    Only legacy links that can be tied back to an existing Payment Request are
    accepted; all client-supplied checkout parameters are ignored once the
    Payment Request is identified.
    """
    params = frappe._dict(params or {})
    reference_doctype = str(params.get("reference_doctype") or "").strip()
    reference_docname = str(params.get("reference_docname") or "").strip()
    order_id = str(params.get("order_id") or "").strip()
    payment_request_id = str(params.get("payment_request_id") or "").strip()

    payment_request_name = ""
    if payment_request_id:
        payment_request_name = payment_request_id
    elif order_id:
        payment_request_name = order_id
    elif reference_doctype == "Payment Request" and reference_docname:
        payment_request_name = reference_docname

    if not payment_request_name:
        return None

    if not frappe.db.exists("Payment Request", payment_request_name):
        return None

    payment_request = frappe.db.get_value(
        "Payment Request",
        payment_request_name,
        ["reference_doctype", "reference_name", "docstatus", "status"],
        as_dict=1,
    )
    if not payment_request:
        return None

    if payment_request.docstatus == 2 or payment_request.status in ("Paid", "Cancelled"):
        return None

    if reference_doctype == "Payment Request":
        if reference_docname and payment_request_name != reference_docname:
            return None
    elif reference_doctype or reference_docname:
        if (
            reference_doctype != payment_request.reference_doctype
            or reference_docname != payment_request.reference_name
        ):
            return None

    settings_name = frappe.db.get_value("LemonSqueezy Settings", {"enabled": 1}, "name")
    if not settings_name:
        frappe.throw(_("No enabled LemonSqueezy Settings found"))

    token = issue_checkout_token(payment_request_name, settings_name)
    return get_checkout_redirect_url(token)


def _get_payment_success_redirect(reference_doctype, reference_docname, message):
    return frappe.utils.get_url(
        "/payment-success?{0}".format(
            urlencode(
                {
                    "doctype": reference_doctype,
                    "docname": reference_docname,
                    "redirect_message": message,
                }
            )
        )
    )


def _resolve_variant_from_subscription(subscription_name, checkout_kwargs, settings):
    plans = frappe.get_all(
        "Subscription Plan Detail",
        filters={"parent": subscription_name},
        fields=["plan"],
        limit=1,
    )
    if not plans:
        return

    plan_id = plans[0].plan
    variant_id = frappe.db.get_value("Subscription Plan", plan_id, "product_price_id")
    if variant_id:
        checkout_kwargs["variant_id"] = variant_id
        if getattr(settings, "verbose_logging", False):
            frappe.log_error(
                f"Found Variant ID {variant_id} from Subscription Plan {plan_id}",
                "LemonSqueezy Debug",
            )


def _get_checkout_amount_from_item_row(item_row):
    for fieldname in ("net_amount", "amount", "base_net_amount", "base_amount"):
        amount = flt(item_row.get(fieldname))
        if amount > 0:
            return amount
    return None


def _get_available_item_fields(item_doctype):
    requested_fields = [
        "item_code",
        "subscription_plan",
        "net_amount",
        "amount",
        "base_net_amount",
        "base_amount",
    ]
    return [fieldname for fieldname in requested_fields if frappe.db.has_column(item_doctype, fieldname)]


def _apply_sales_document_item_checkout_data(reference_doctype, reference_docname, checkout_kwargs, settings):
    item_doctype = {
        "Sales Invoice": "Sales Invoice Item",
        "Sales Order": "Sales Order Item",
    }.get(reference_doctype)
    if not item_doctype:
        return

    item_fields = _get_available_item_fields(item_doctype)
    if not item_fields:
        return

    items = frappe.get_all(
        item_doctype,
        filters={"parent": reference_docname},
        fields=item_fields,
        order_by="idx asc",
        limit=1,
    )
    if not items:
        return

    item_row = items[0]
    item_amount = _get_checkout_amount_from_item_row(item_row)
    item_variant_id = None

    if item_row.item_code:
        item_variant_id = frappe.db.get_value("Item", item_row.item_code, "lemonsqueezy_variant_id")

    if not item_variant_id and item_row.subscription_plan:
        item_variant_id = frappe.db.get_value(
            "Subscription Plan",
            item_row.subscription_plan,
            "product_price_id",
        )

    if item_variant_id:
        checkout_kwargs["variant_id"] = item_variant_id
        if item_amount is not None:
            checkout_kwargs["amount"] = item_amount
        if getattr(settings, "verbose_logging", False):
            frappe.log_error(
                f"Using invoice item amount {item_amount} for variant {item_variant_id}",
                "LemonSqueezy Debug",
            )


def _apply_reference_checkout_data(payment_request, checkout_kwargs, settings):
    reference_doctype = payment_request.reference_doctype
    reference_docname = payment_request.reference_name

    if reference_doctype in ["Sales Order", "Sales Invoice"] and reference_docname:
        try:
            doc = frappe.get_doc(reference_doctype, reference_docname)

            if reference_doctype == "Sales Order":
                outstanding_amount = flt(doc.grand_total) - flt(doc.advance_paid)
                if outstanding_amount <= 0.01 or doc.status in ["Completed", "Closed"]:
                    return {
                        "redirect_url": _get_payment_success_redirect(
                            reference_doctype,
                            reference_docname,
                            _("This order has already been paid."),
                        )
                    }

                if outstanding_amount > 0:
                    checkout_kwargs["amount"] = outstanding_amount

            elif reference_doctype == "Sales Invoice":
                outstanding_amount = flt(doc.outstanding_amount)
                if outstanding_amount <= 0 or doc.status == "Paid":
                    return {
                        "redirect_url": _get_payment_success_redirect(
                            reference_doctype,
                            reference_docname,
                            _("This invoice has already been paid."),
                        )
                    }

                if outstanding_amount > 0:
                    checkout_kwargs["amount"] = outstanding_amount
        except Exception as exc:
            frappe.log_error(f"Error checking payment status: {str(exc)}", "LemonSqueezy")

    if reference_doctype in ("Sales Invoice", "Sales Order"):
        _apply_sales_document_item_checkout_data(
            reference_doctype,
            reference_docname,
            checkout_kwargs,
            settings,
        )

    if reference_doctype == "Sales Invoice" and not checkout_kwargs.get("variant_id"):
        subscription_name = frappe.db.get_value("Sales Invoice", reference_docname, "subscription")
        if subscription_name:
            _resolve_variant_from_subscription(subscription_name, checkout_kwargs, settings)

    elif reference_doctype == "Subscription" and reference_docname:
        _resolve_variant_from_subscription(reference_docname, checkout_kwargs, settings)

    return {"redirect_url": None}


def build_checkout_request(payment_request_name, settings):
    if not frappe.db.exists("Payment Request", payment_request_name):
        frappe.throw(_("Payment Request {0} was not found.").format(payment_request_name))

    payment_request = frappe.get_doc("Payment Request", payment_request_name)
    if payment_request.status in ("Paid", "Cancelled"):
        frappe.throw(_("Payment Request {0} is not payable.").format(payment_request_name))

    checkout_kwargs = {
        "payment_request_id": payment_request.name,
        "reference_doctype": "Payment Request",
        "reference_docname": payment_request.name,
    }

    amount = (
        payment_request.get("grand_total")
        or payment_request.get("payment_amount")
        or payment_request.get("total_amount_to_pay")
        or 0
    )
    if amount:
        checkout_kwargs["amount"] = amount

    if payment_request.get("currency"):
        checkout_kwargs["currency"] = payment_request.currency

    if payment_request.get("email_to"):
        checkout_kwargs["payer_email"] = payment_request.email_to

    payer_name = (
        payment_request.get("party_name")
        or payment_request.get("customer_name")
        or payment_request.get("contact_display")
    )
    if payer_name:
        checkout_kwargs["payer_name"] = payer_name

    result = _apply_reference_checkout_data(payment_request, checkout_kwargs, settings)

    return {
        "payment_request": payment_request,
        "checkout_kwargs": checkout_kwargs,
        "redirect_url": result.get("redirect_url"),
    }


def resolve_checkout_request_from_token(token):
    payload = validate_checkout_token(token)

    settings_name = payload["settings"]
    if not frappe.db.exists("LemonSqueezy Settings", settings_name):
        frappe.throw(_("Checkout configuration is not available."))

    settings = frappe.get_doc("LemonSqueezy Settings", settings_name)
    if not settings.enabled:
        frappe.throw(_("Checkout configuration is disabled."))

    checkout_request = build_checkout_request(payload["payment_request"], settings)
    checkout_request["settings"] = settings
    return checkout_request
