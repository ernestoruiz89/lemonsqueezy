import frappe
import hmac
import hashlib
import json
from frappe import _
from frappe.utils import get_datetime, nowdate, flt
from erpnext.setup.utils import get_exchange_rate

from lemonsqueezy.lemonsqueezy.checkout import (
    get_legacy_checkout_redirect_url,
    resolve_checkout_request_from_token,
)

# Supported webhook events
SUPPORTED_EVENTS = [
    "order_created",
    "subscription_created",
    "subscription_updated",
    "subscription_cancelled",
    "subscription_resumed",
    "subscription_expired",
    "subscription_paused",
    "subscription_unpaused",
    "subscription_payment_success",
    "subscription_payment_failed"
]

MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024  # 2MB safeguard to prevent oversized payloads
WEBHOOK_PROCESSING_SAVEPOINT = "lemonsqueezy_webhook_processing"

# Sensitive fields to remove from webhook payload when sanitizing
SENSITIVE_FIELDS = [
    "user_email", "customer_email", "billing_address", "shipping_address",
    "card_brand", "card_last_four", "ip_address", "user_agent"
]

def sanitize_payload(data):
    """
    Remove sensitive data from webhook payload before storing.
    Creates a deep copy to avoid modifying the original data.
    """
    import copy
    sanitized = copy.deepcopy(data)
    
    def _sanitize_dict(d):
        if not isinstance(d, dict):
            return
        for key in list(d.keys()):
            if key in SENSITIVE_FIELDS:
                d[key] = "[REDACTED]"
            elif isinstance(d[key], dict):
                _sanitize_dict(d[key])
            elif isinstance(d[key], list):
                for item in d[key]:
                    if isinstance(item, dict):
                        _sanitize_dict(item)
    
    _sanitize_dict(sanitized)
    return sanitized

def debug_log(settings, message, title="LemonSqueezy Debug"):
    """
    Log debug message only if verbose_logging is enabled in settings.
    """
    if settings and getattr(settings, 'verbose_logging', False):
        frappe.log_error(message, title)

def build_webhook_idempotency_key(data, raw_body):
    """Build a stable idempotency key for the webhook payload."""
    meta = data.get("meta", {})
    event_name = meta.get("event_name") or frappe.request.headers.get("X-Event-Name") or "unknown"
    resource_id = str(data.get("data", {}).get("id") or "").strip()
    event_id = (
        meta.get("event_id")
        or meta.get("webhook_event_id")
        or frappe.request.headers.get("X-Event-Id")
    )

    if event_name == "order_created" and resource_id:
        return f"{event_name}:{resource_id}", resource_id

    if event_id:
        event_id = str(event_id).strip()
        return f"{event_name}:{event_id}", resource_id or event_id

    payload_hash = hashlib.sha256(raw_body or json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    if resource_id:
        return f"{event_name}:{resource_id}:{payload_hash}", resource_id

    return f"{event_name}:{payload_hash}", payload_hash

def get_webhook_log_row(idempotency_key, for_update=False):
    """Fetch an existing webhook log row, optionally locking it."""
    query = """
        SELECT name, status
        FROM `tabLemonSqueezy Webhook Log`
        WHERE idempotency_key = %s
        LIMIT 1
    """
    if for_update:
        query += " FOR UPDATE"

    rows = frappe.db.sql(query, (idempotency_key,), as_dict=1)
    return rows[0] if rows else None

def reserve_webhook_log(event_name, payload, idempotency_key, resource_id):
    """
    Reserve a webhook log row before processing so duplicate deliveries do not
    re-run side effects. Failed rows can be retried by reusing the same record.
    """
    payload_json = json.dumps(payload, indent=2)

    def _reuse_existing(row):
        log_doc = frappe.get_doc("LemonSqueezy Webhook Log", row.name)
        if log_doc.status in ("Success", "Processing"):
            return log_doc, False

        log_doc.event_name = event_name
        log_doc.resource_id = resource_id
        log_doc.payload = payload_json
        log_doc.status = "Processing"
        log_doc.error_message = None
        log_doc.payment_entry = None
        log_doc.save(ignore_permissions=True)
        return log_doc, True

    existing = get_webhook_log_row(idempotency_key, for_update=True)
    if existing:
        return _reuse_existing(existing)

    log_doc = frappe.get_doc(
        {
            "doctype": "LemonSqueezy Webhook Log",
            "event_name": event_name,
            "idempotency_key": idempotency_key,
            "resource_id": resource_id,
            "payload": payload_json,
            "status": "Processing",
        }
    )

    try:
        log_doc.insert(ignore_permissions=True)
        return log_doc, True
    except Exception:
        existing = get_webhook_log_row(idempotency_key, for_update=True)
        if existing:
            return _reuse_existing(existing)
        raise

def get_existing_payment_entry(order_id):
    """Return an existing Payment Entry already linked to this LemonSqueezy order."""
    payment_entry = frappe.db.get_value(
        "Payment Entry",
        {
            "reference_no": str(order_id),
            "docstatus": ["<", 2],
        },
        ["name", "docstatus"],
        as_dict=1,
    )
    if not payment_entry:
        return None

    return frappe.get_doc("Payment Entry", payment_entry.name)

@frappe.whitelist(allow_guest=True)
def handle_webhook():
    """
    Handle LemonSqueezy Webhooks
    Endpoint: /api/method/lemonsqueezy.lemonsqueezy.api.handle_webhook
    """
    
    # Get signature from headers
    signature = frappe.request.headers.get("X-Signature")
    if not signature:
            frappe.log_error("No signature provided in LemonSqueezy webhook", "LemonSqueezy Webhook Error")
            frappe.local.response['http_status_code'] = 401
            return {"status": "error", "message": "No signature provided"}

    raw_body = frappe.request.get_data()

    if not raw_body:
            frappe.log_error("Empty body received in LemonSqueezy webhook", "LemonSqueezy Webhook Error")
            frappe.local.response['http_status_code'] = 400
            return {"status": "error", "message": "Empty body"}

    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        frappe.log_error(
                f"Webhook body exceeded size limit ({len(raw_body)} bytes)",
                "LemonSqueezy Webhook Error",
        )
        frappe.local.response['http_status_code'] = 413
        return {"status": "error", "message": "Payload too large"}

    # Find the correct settings doc that matches the signature
    settings_list = frappe.get_all("LemonSqueezy Settings", fields=["name"], filters={"enabled": 1})
    
    if not settings_list:
        frappe.log_error("No enabled LemonSqueezy Settings found", "LemonSqueezy Webhook Error")
        frappe.local.response['http_status_code'] = 401
        return {"status": "error", "message": "No enabled settings"}
    
    valid_settings = None
    
    for settings_doc in settings_list:
        try:
            settings = frappe.get_doc("LemonSqueezy Settings", settings_doc.name)
            webhook_secret = settings.get_password("webhook_secret")
            
            if not webhook_secret:
                continue
                
            secret = webhook_secret.encode('utf-8')
            digest = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
            
            if hmac.compare_digest(digest, signature):
                valid_settings = settings
                break
        except Exception as e:
            frappe.log_error(f"Error validating signature for {settings_doc.name}: {str(e)}")
            continue
            
    if not valid_settings:
        frappe.log_error("Invalid signature in LemonSqueezy webhook", "LemonSqueezy Webhook Error")
        frappe.local.response['http_status_code'] = 401
        return {"status": "error", "message": "Invalid signature"}
        
    # Process payload
    try:
        data = json.loads(raw_body)
    except Exception as e:
        frappe.log_error(f"Invalid JSON in webhook: {str(e)}", "LemonSqueezy Webhook Error")
        frappe.local.response['http_status_code'] = 400
        return {"status": "error", "message": "Invalid JSON"}

    event_name = data.get("meta", {}).get("event_name")

    if not event_name:
            frappe.log_error("Missing event_name in LemonSqueezy webhook metadata", "LemonSqueezy Webhook Error")
            frappe.local.response['http_status_code'] = 400
            return {"status": "error", "message": "Invalid event"}

    # Validate event is supported
    if event_name not in SUPPORTED_EVENTS:
            frappe.log_error(
                    f"Unsupported event type: {event_name}",
                    "LemonSqueezy Webhook",
            )
            return {"status": "success", "message": "Event not supported"}
    
    # Prepare payload for logging (optionally sanitized)
    log_payload = data
    if getattr(valid_settings, 'sanitize_webhook_payload', False):
        log_payload = sanitize_payload(data)

    idempotency_key, resource_id = build_webhook_idempotency_key(data, raw_body)
    log_doc, should_process = reserve_webhook_log(
        event_name=event_name,
        payload=log_payload,
        idempotency_key=idempotency_key,
        resource_id=resource_id,
    )

    if not should_process:
        frappe.db.rollback()
        return {"status": "success", "message": "Event already processed"}

    frappe.db.sql(f"SAVEPOINT {WEBHOOK_PROCESSING_SAVEPOINT}")

    # Process event
    try:
        result = {}
        if event_name == "order_created":
            result = process_order_created(data, valid_settings) or {}
        elif event_name in ["subscription_created", "subscription_updated", "subscription_cancelled", "subscription_resumed", "subscription_expired", "subscription_paused", "subscription_unpaused", "subscription_payment_success", "subscription_payment_failed"]:
            process_subscription_event(data, valid_settings, event_name)

        if result.get("payment_entry_name"):
            log_doc.payment_entry = result["payment_entry_name"]
        log_doc.status = "Success"
        log_doc.error_message = None
        log_doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.db.sql(f"ROLLBACK TO SAVEPOINT {WEBHOOK_PROCESSING_SAVEPOINT}")
        error_msg = f"Error processing {event_name}: {str(e)}\\n{frappe.get_traceback()}"
        frappe.log_error(error_msg, "LemonSqueezy Webhook Error")
        
        # Update log with error
        log_doc = frappe.get_doc("LemonSqueezy Webhook Log", log_doc.name)
        log_doc.status = "Failed"
        log_doc.error_message = error_msg
        log_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response['http_status_code'] = 500
        return {"status": "error", "message": str(e)}
    
    return {"status": "success"}

def process_order_created(data, settings):
    """Process order_created webhook event"""
    order_data = data.get("data", {})
    attributes = order_data.get("attributes", {})
    payment_entry_name = None

    paid_amount = (attributes.get("total") or 0) / 100
    paid_currency = (attributes.get("currency") or "USD").upper()
    order_id = str(order_data.get("id"))

    custom_data = data.get("meta", {}).get("custom_data", {})
    payment_request_id = custom_data.get("payment_request_id")

    # Update Payment Request if exists
    if payment_request_id:
        try:
            if not frappe.db.exists("Payment Request", payment_request_id):
                frappe.log_error(f"Payment Request {payment_request_id} not found")
            else:
                pr = frappe.get_doc("Payment Request", payment_request_id)
                should_mark_paid = True
                expected_amount = (
                    pr.get("payment_amount")
                    or pr.get("grand_total")
                    or pr.get("total_amount_to_pay")
                )
                expected_currency = (pr.get("currency") or "").upper()

                if expected_amount:
                    expected_amount = float(expected_amount)
                    if paid_amount + 0.009 < expected_amount:
                        frappe.log_error(
                            f"Partial payment received for Payment Request {payment_request_id}: "
                            f"expected {expected_amount} but received {paid_amount} {paid_currency}",
                            "LemonSqueezy Webhook",
                        )
                        should_mark_paid = False

                if expected_currency and expected_currency != paid_currency:
                    frappe.log_error(
                        f"Currency mismatch for Payment Request {payment_request_id}: "
                        f"expected {expected_currency} but received {paid_currency}",
                        "LemonSqueezy Webhook",
                    )
                    should_mark_paid = False

                # Verify payment status from LemonSqueezy order
                order_status = attributes.get("status")
                debug_log(settings, f"LemonSqueezy Order Status: {order_status} for Order {order_id}")
                
                # Only mark as paid if LemonSqueezy confirms payment is complete
                if order_status != "paid":
                    frappe.log_error(
                        f"Order {order_id} status is '{order_status}', not 'paid'. Payment Request will not be marked as paid.",
                        "LemonSqueezy Webhook"
                    )
                    should_mark_paid = False

                if should_mark_paid:
                    # Create or reuse Payment Entry before marking the Payment Request as paid.
                    current_user = frappe.session.user
                    try:
                        frappe.set_user("Administrator")

                        payment_entry = get_existing_payment_entry(order_id)
                        if payment_entry:
                            if payment_entry.docstatus == 0:
                                payment_entry.submit()
                            debug_log(
                                settings,
                                f"Reusing existing Payment Entry {payment_entry.name} for Order {order_id}",
                            )
                        else:
                            from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

                            # Get company and accounts from Payment Request
                            company = pr.company
                            payment_account = pr.payment_account

                            # Handle Currency Conversion for Paid Amount
                            source_amount = flt(pr.grand_total)
                            source_currency = pr.currency
                            target_currency = frappe.get_value("Account", payment_account, "account_currency") or frappe.get_cached_value('Company',  company,  "default_currency")

                            paid_amount = source_amount
                            if source_currency != target_currency:
                                exchange_rate = get_exchange_rate(source_currency, target_currency, get_datetime(attributes.get("created_at")).date() if attributes.get("created_at") else nowdate())
                                paid_amount = flt(source_amount) * flt(exchange_rate)

                            # Create Payment Entry using ERPNext utility
                            # This automatically handles outstanding amount, currency, and references
                            payment_entry = get_payment_entry(
                                dt=pr.reference_doctype,
                                dn=pr.reference_name,
                                bank_account=payment_account,
                                bank_amount=paid_amount
                            )

                            # Update details
                            payment_entry.payment_type = "Receive"
                            payment_entry.mode_of_payment = "LemonSqueezy" if frappe.db.exists("Mode of Payment", "LemonSqueezy") else payment_entry.mode_of_payment
                            payment_entry.reference_no = order_id
                            payment_entry.reference_date = get_datetime(attributes.get("created_at")).date() if attributes.get("created_at") else nowdate()
                            payment_entry.posting_date = payment_entry.reference_date

                            # Ensure amounts are correct (get_payment_entry might default to outstanding)
                            payment_entry.paid_amount = flt(paid_amount)
                            payment_entry.received_amount = flt(paid_amount)

                            # Adjust allocation if necessary
                            # get_payment_entry sets allocated_amount = outstanding_amount
                            # We need to ensure allocated_amount <= paid_amount
                            if payment_entry.references:
                                for ref in payment_entry.references:
                                    if ref.allocated_amount > payment_entry.paid_amount:
                                        ref.allocated_amount = payment_entry.paid_amount

                            # Add remarks
                            payment_entry.remarks = f"Payment received via LemonSqueezy for {pr.reference_doctype} {pr.reference_name}. Order ID: {order_id}. Amount: {source_amount} {source_currency} -> {paid_amount} {target_currency}"

                            # Insert and submit payment entry
                            payment_entry.insert(ignore_permissions=True)
                            payment_entry.submit()

                            debug_log(settings, f"Payment Entry {payment_entry.name} created for Order {order_id}")

                        payment_entry_name = payment_entry.name

                        if pr.status != "Paid":
                            pr.status = "Paid"
                            pr.db_set("status", "Paid")
                            pr.run_method("on_payment_authorized", "Completed")
                    except Exception as pe_error:
                        frappe.log_error(
                            f"Error creating Payment Entry for PR {payment_request_id}: {str(pe_error)}\n{frappe.get_traceback()}",
                            "LemonSqueezy Payment Entry Error"
                        )
                        raise
                    finally:
                        # Restore original user
                        frappe.set_user(current_user)
        except Exception as e:
            frappe.log_error(f"Error processing order_created for PR {payment_request_id}: {str(e)}")
            raise

    # Store order data in LemonSqueezy Order
    try:
        # Check if order already exists
        if frappe.db.exists("LemonSqueezy Order", {"order_id": order_id}):
            return {"payment_entry_name": payment_entry_name}
        
        # Validate currency exists in system
        currency_code = (attributes.get("currency") or "USD").upper()
        if not frappe.db.exists("Currency", currency_code):
            frappe.log_error(f"Currency {currency_code} not found, defaulting to USD", "LemonSqueezy Validation")
            currency_code = "USD"
        
        # Create new order
        order_doc = frappe.new_doc("LemonSqueezy Order")
        order_doc.order_id = order_id
        order_doc.status = "Paid"
        order_doc.customer_email = attributes.get("user_email")
        
        # Amounts (convert from cents to currency)
        order_doc.total = (attributes.get("total") or 0) / 100
        order_doc.subtotal = (attributes.get("subtotal") or 0) / 100
        order_doc.discount_total = (attributes.get("discount_total") or 0) / 100
        order_doc.tax = (attributes.get("tax") or 0) / 100
        order_doc.currency = currency_code
        
        # Dates
        dt = get_datetime(attributes.get("created_at"))
        order_doc.order_date = dt.replace(tzinfo=None) if dt else None
        
        # Product info
        first_item = attributes.get("first_order_item", {})
        order_doc.product_id = str(first_item.get("product_id")) if first_item.get("product_id") else None
        order_doc.variant_id = str(first_item.get("variant_id")) if first_item.get("variant_id") else None
        order_doc.product_name = first_item.get("product_name")
        order_doc.variant_name = first_item.get("variant_name")
        
        # Subscription info
        subscription_id = attributes.get("first_subscription_item", {}).get("subscription_id")
        if subscription_id:
            order_doc.subscription_id = str(subscription_id)
            order_doc.is_subscription = 1
            
            # Try to get billing interval from subscription
            try:
                sub = frappe.db.get_value(
                    "LemonSqueezy Subscription",
                    {"subscription_id": str(subscription_id)},
                    ["variant_name"],
                    as_dict=1
                )
                if sub and sub.variant_name:
                    # Try to detect interval from variant name
                    variant_lower = sub.variant_name.lower()
                    if "month" in variant_lower:
                        order_doc.billing_interval = "Monthly"
                    elif "year" in variant_lower:
                        order_doc.billing_interval = "Yearly"
                    elif "week" in variant_lower:
                        order_doc.billing_interval = "Weekly"
            except:
                pass
        
        # First order check
        order_doc.first_order = attributes.get("first_order_item", {}).get("price_id") is not None
        
        order_doc.insert(ignore_permissions=True)
        
    except Exception as e:
        frappe.log_error(f"Error storing order data: {str(e)}\\n{frappe.get_traceback()}", "LemonSqueezy Order Error")
        raise

    return {"payment_entry_name": payment_entry_name}

def process_subscription_event(data, settings, event_name):
    """Process subscription-related webhook events"""
    subscription_data = data.get("data", {})
    attributes = subscription_data.get("attributes", {})
    
    # Handle payment events where data is an invoice, not the subscription itself
    if event_name in ["subscription_payment_success", "subscription_payment_failed"]:
        subscription_id = str(attributes.get("subscription_id"))
        # Invoice status is 'paid'/'pending'/'failed', not subscription status
        # We don't update subscription status based on invoice status directly here
        status = None 
    else:
        subscription_id = str(subscription_data.get("id"))
        status = attributes.get("status")

    if not subscription_id:
        frappe.log_error("No subscription_id in webhook data")
        return
        
    customer_id = attributes.get("customer_id")
    product_id = attributes.get("product_id")
    variant_id = attributes.get("variant_id")
    
    # Dates
    renews_at = attributes.get("renews_at")
    ends_at = attributes.get("ends_at")
    trial_ends_at = attributes.get("trial_ends_at")
    
    # URLs
    urls = attributes.get("urls", {})
    update_url = urls.get("update_payment_method")
    cancel_url = urls.get("customer_portal")
    
    # Product info
    product_name = attributes.get("product_name")
    variant_name = attributes.get("variant_name")
    user_email = attributes.get("user_email")
    order_id = attributes.get("order_id")
    
    # Check if subscription exists using proper query
    existing_name = frappe.db.get_value("LemonSqueezy Subscription", {"subscription_id": subscription_id}, "name")
    
    if existing_name:
        doc = frappe.get_doc("LemonSqueezy Subscription", existing_name)
    else:
        # If it's a payment event and subscription doesn't exist, we can't create it properly without status
        if event_name in ["subscription_payment_success", "subscription_payment_failed"]:
            frappe.log_error(f"Received {event_name} for unknown subscription {subscription_id}", "LemonSqueezy Webhook")
            return {"status": "success", "message": "Subscription not found, skipping payment event"}
            
        doc = frappe.new_doc("LemonSqueezy Subscription")
        doc.subscription_id = subscription_id
        # Set required fields for new documents
        if not status:
            frappe.throw(_("Status is required for new subscription"))
        doc.status = status
        if user_email:
            doc.customer_email = user_email
        
    # Update fields conditionally (only for existing docs)
    if existing_name:
        if status:
            doc.status = status
        if user_email:
            doc.customer_email = user_email
    
    if product_id:
        doc.product_id = str(product_id)
    if variant_id:
        doc.variant_id = str(variant_id)
    if product_name:
        doc.product_name = product_name
    if variant_name:
        doc.variant_name = variant_name
    if order_id:
        doc.order_id = str(order_id)
    
    if renews_at:
        dt = get_datetime(renews_at)
        doc.renews_at = dt.replace(tzinfo=None) if dt else None
    if ends_at:
        dt = get_datetime(ends_at)
        doc.ends_at = dt.replace(tzinfo=None) if dt else None
    if trial_ends_at:
        dt = get_datetime(trial_ends_at)
        doc.trial_ends_at = dt.replace(tzinfo=None) if dt else None
        
    if update_url:
        doc.update_url = update_url
    if cancel_url:
        doc.cancel_url = cancel_url
    
    # Financials (from payment events or if available)
    if "total" in attributes:
        doc.total = (attributes.get("total") or 0) / 100
    if "subtotal" in attributes:
        doc.subtotal = (attributes.get("subtotal") or 0) / 100
    if "tax" in attributes:
        doc.tax = (attributes.get("tax") or 0) / 100
    if "currency" in attributes:
        currency_code = (attributes.get("currency") or "USD").upper()
        if frappe.db.exists("Currency", currency_code):
            doc.currency = currency_code
        else:
            doc.currency = "USD"
        
    # Billing Interval
    if variant_name:
        variant_lower = variant_name.lower()
        if "month" in variant_lower:
            doc.billing_interval = "Monthly"
        elif "year" in variant_lower:
            doc.billing_interval = "Yearly"
        elif "week" in variant_lower:
            doc.billing_interval = "Weekly"

    # Try to link to a Customer if email matches
    if not doc.customer and user_email:
        # First try direct email_id match on Customer
        customer = frappe.db.get_value("Customer", {"email_id": user_email}, "name")
        
        # If not found, search in Contact with link to Customer
        if not customer:
            contact = frappe.db.sql("""
                SELECT dl.link_name 
                FROM `tabContact` c
                JOIN `tabContact Email` ce ON ce.parent = c.name
                JOIN `tabDynamic Link` dl ON dl.parent = c.name
                WHERE ce.email_id = %s 
                AND dl.link_doctype = 'Customer'
                LIMIT 1
            """, (user_email,), as_dict=1)
            if contact:
                customer = contact[0].link_name
        
        if customer:
            doc.customer = customer
            
    doc.save(ignore_permissions=True)

@frappe.whitelist(allow_guest=True)
def lemonsqueezy_checkout(token=None, **kwargs):
    """
    Redirect to LemonSqueezy Checkout
    Endpoint: /api/method/lemonsqueezy.lemonsqueezy.api.lemonsqueezy_checkout
    """
    try:
        if kwargs:
            legacy_redirect_url = get_legacy_checkout_redirect_url(kwargs)
            if legacy_redirect_url:
                frappe.local.response["type"] = "redirect"
                frappe.local.response["location"] = legacy_redirect_url
                return
            frappe.throw(_("Checkout links no longer accept free-form parameters."))
        if not token:
            frappe.throw(_("A valid checkout token is required."))

        checkout_request = resolve_checkout_request_from_token(token)
        redirect_url = checkout_request.get("redirect_url")
        if redirect_url:
            frappe.local.response["type"] = "redirect"
            frappe.local.response["location"] = redirect_url
            return

        settings = checkout_request["settings"]
        checkout_url = settings.get_api_checkout_url(**checkout_request["checkout_kwargs"])

        if checkout_url:
            frappe.local.response["type"] = "redirect"
            frappe.local.response["location"] = checkout_url
        else:
            frappe.throw(_("Could not generate LemonSqueezy checkout URL"))

    except Exception as e:
        error_msg = str(e)
        # Use explicit title and message to avoid length issues
        frappe.log_error(message=f"LemonSqueezy Checkout Error: {error_msg[:500]}", title="LemonSqueezy Checkout Error")
        frappe.throw(_("Error initiating checkout. Please check Error Log for details."))
