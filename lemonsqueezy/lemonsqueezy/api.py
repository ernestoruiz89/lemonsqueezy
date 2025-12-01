import frappe
import hmac
import hashlib
import json
from frappe import _
from frappe.utils import get_datetime, nowdate, flt
from erpnext.setup.utils import get_exchange_rate

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

    # Log minimal context without storing full payload to avoid leaking sensitive data
    meta = data.get("meta") or {}
    frappe.log_error(
            f"Received webhook event: {meta.get('event_name')} for store {meta.get('store_id')}",
            "LemonSqueezy Webhook Debug",
    )

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
    
    # Create Webhook Log
    log_doc = frappe.get_doc({
        "doctype": "LemonSqueezy Webhook Log",
        "event_name": event_name,
        "payload": json.dumps(data, indent=2),
        "status": "Success"
    })
    log_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    
    # Process event
    try:
        if event_name == "order_created":
            process_order_created(data, valid_settings)
        elif event_name in ["subscription_created", "subscription_updated", "subscription_cancelled", "subscription_resumed", "subscription_expired", "subscription_paused", "subscription_unpaused", "subscription_payment_success", "subscription_payment_failed"]:
            process_subscription_event(data, valid_settings, event_name)
            
    except Exception as e:
        error_msg = f"Error processing {event_name}: {str(e)}\\n{frappe.get_traceback()}"
        frappe.log_error(error_msg, "LemonSqueezy Webhook Error")
        
        # Update log with error
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
                frappe.log_error(f"LemonSqueezy Order Status: {order_status} for Order {order_id}", "LemonSqueezy Debug")
                
                # Only mark as paid if LemonSqueezy confirms payment is complete
                if order_status != "paid":
                    frappe.log_error(
                        f"Order {order_id} status is '{order_status}', not 'paid'. Payment Request will not be marked as paid.",
                        "LemonSqueezy Webhook"
                    )
                    should_mark_paid = False

                if should_mark_paid and pr.status != "Paid":
                    # Create Payment Entry manually
                    try:
                        # Switch to Administrator to bypass permission checks
                        current_user = frappe.session.user
                        frappe.set_user("Administrator")
                        
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
                        
                        frappe.log_error(f"Payment Entry {payment_entry.name} created for Order {order_id}", "LemonSqueezy Payment Success")
                        
                        # Only update Payment Request status if Payment Entry was created successfully
                        pr.status = "Paid"
                        pr.db_set("status", "Paid")
                        pr.run_method("on_payment_authorized", "Completed")
                        frappe.db.commit()
                        
                    except Exception as pe_error:
                        frappe.log_error(
                            f"Error creating Payment Entry for PR {payment_request_id}: {str(pe_error)}\n{frappe.get_traceback()}",
                            "LemonSqueezy Payment Entry Error"
                        )
                        # Do NOT mark PR as paid if Payment Entry creation fails
                        # Leave it in its current state so it can be retried
                    finally:
                        # Restore original user
                        frappe.set_user(current_user)
        except Exception as e:
            frappe.log_error(f"Error processing order_created for PR {payment_request_id}: {str(e)}")

    # Store order data in LemonSqueezy Order
    try:
        order_id = str(order_data.get("id"))
        
        # Check if order already exists
        if frappe.db.exists("LemonSqueezy Order", {"order_id": order_id}):
            return
        
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
        order_doc.currency = (attributes.get("currency") or "USD").upper()
        
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
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(f"Error storing order data: {str(e)}\\n{frappe.get_traceback()}", "LemonSqueezy Order Error")

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
        doc.currency = (attributes.get("currency") or "USD").upper()
        
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
        customer = frappe.db.get_value("Customer", {"email_id": user_email}, "name")
        if customer:
            doc.customer = customer
            
    doc.save(ignore_permissions=True)
    frappe.db.commit()

@frappe.whitelist(allow_guest=True)
def lemonsqueezy_checkout(**kwargs):

    """
    Redirect to LemonSqueezy Checkout
    Endpoint: /api/method/lemonsqueezy.lemonsqueezy.api.lemonsqueezy_checkout
    """
    try:
        # Get settings
        # Since LemonSqueezy Settings is not a Single DocType, we need to find the enabled one
        settings_name = frappe.db.get_value("LemonSqueezy Settings", {"enabled": 1}, "name")
        if not settings_name:
            frappe.throw(_("No enabled LemonSqueezy Settings found"))

        settings = frappe.get_doc("LemonSqueezy Settings", settings_name)

        payment_request_id = None

        if not kwargs.get("variant_id") and not settings.default_variant_id and not (
            kwargs.get("reference_doctype") and kwargs.get("reference_docname")
        ):
            frappe.throw(_("A valid reference or variant_id is required to start checkout."))

        # Try to find Variant ID from Reference Document (Subscription Plan)
        if not kwargs.get("variant_id") and kwargs.get("reference_doctype") and kwargs.get("reference_docname"):
            try:
                ref_dt = kwargs.get("reference_doctype")
                ref_dn = kwargs.get("reference_docname")

                # If reference is Payment Request, get the actual reference (Subscription/Invoice)
                if ref_dt == "Payment Request":
                    payment_request_id = ref_dn
                    pr_name = kwargs.get("reference_docname")
                    if not frappe.db.exists("Payment Request", pr_name):
                        frappe.throw(_("Payment Request {0} was not found.").format(pr_name))

                    pr = frappe.db.get_value(
                        "Payment Request",
                        pr_name,
                        ["reference_doctype", "reference_name", "status"],
                        as_dict=1,
                    )

                    if pr:
                        if pr.status in ("Paid", "Cancelled"):
                            frappe.throw(_("Payment Request {0} is not payable.").format(pr_name))

                        ref_dt = pr.reference_doctype
                        ref_dn = pr.reference_name

                # Check if Sales Order or Sales Invoice is already fully paid
                if ref_dt in ["Sales Order", "Sales Invoice"]:
                    try:
                        doc = frappe.get_doc(ref_dt, ref_dn)
                        outstanding_amount = 0
                        
                        # For Sales Order, calculate outstanding amount
                        if ref_dt == "Sales Order":
                            outstanding_amount = flt(doc.grand_total) - flt(doc.advance_paid)
                            if outstanding_amount <= 0.01 or doc.status in ["Completed", "Closed"]:
                                frappe.local.response["type"] = "redirect"
                                frappe.local.response["location"] = frappe.utils.get_url(
                                    "/payment-success?doctype={}&docname={}&redirect_message={}".format(
                                        ref_dt, 
                                        ref_dn,
                                        frappe.utils.quote("This order has already been paid.")
                                    )
                                )
                                return
                            
                            # Use outstanding amount for payment
                            if outstanding_amount > 0:
                                kwargs["amount"] = outstanding_amount
                                frappe.log_error(f"Using outstanding amount {outstanding_amount} for Sales Order {ref_dn}", "LemonSqueezy Debug")
                        
                        # For Sales Invoice, check outstanding_amount
                        elif ref_dt == "Sales Invoice":
                            outstanding_amount = flt(doc.outstanding_amount)
                            if outstanding_amount <= 0 or doc.status == "Paid":
                                frappe.local.response["type"] = "redirect"
                                frappe.local.response["location"] = frappe.utils.get_url(
                                    "/payment-success?doctype={}&docname={}&redirect_message={}".format(
                                        ref_dt,
                                        ref_dn,
                                        frappe.utils.quote("This invoice has already been paid.")
                                    )
                                )
                                return
                                
                            # Use outstanding amount for payment
                            if outstanding_amount > 0:
                                kwargs["amount"] = outstanding_amount
                                frappe.log_error(f"Using outstanding amount {outstanding_amount} for Sales Invoice {ref_dn}", "LemonSqueezy Debug")
                                
                    except Exception as e:
                        # Log error but continue with payment flow
                        frappe.log_error(f"Error checking payment status: {str(e)}", "LemonSqueezy")

                # Handle Sales Invoice reference
                if ref_dt == "Sales Invoice":
                    # PRIORITY 1: Check if the Item has a specific LemonSqueezy Variant ID
                    items = frappe.get_all("Sales Invoice Item", filters={"parent": ref_dn}, fields=["item_code"], limit=1)
                    if items:
                        item_code = items[0].item_code
                        # Check if this Item has a specific LemonSqueezy Variant ID
                        item_variant_id = frappe.db.get_value("Item", item_code, "lemonsqueezy_variant_id")
                        if item_variant_id:
                            kwargs["variant_id"] = item_variant_id
                            frappe.log_error(f"Found Variant ID {item_variant_id} from Item {item_code}", "LemonSqueezy Debug")
                        else:
                            # PRIORITY 2: No specific variant, check if it's a subscription-based invoice
                            sub_name = frappe.db.get_value("Sales Invoice", ref_dn, "subscription")
                            if sub_name:
                                ref_dt = "Subscription"
                                ref_dn = sub_name
                            else:
                                # PRIORITY 3: Check items for subscription plan
                                sub_items = frappe.get_all("Sales Invoice Item", filters={"parent": ref_dn}, fields=["subscription_plan"])
                                if sub_items and sub_items[0].subscription_plan:
                                    plan_id = sub_items[0].subscription_plan
                                    variant_id = frappe.db.get_value("Subscription Plan", plan_id, "product_price_id")
                                    if variant_id:
                                        kwargs["variant_id"] = variant_id
                                        frappe.log_error(f"Found Variant ID {variant_id} from Invoice Item Plan {plan_id}", "LemonSqueezy Debug")

                # Handle Subscription reference
                if ref_dt == "Subscription":
                    # Get plan from Subscription
                    # 'plans' is a child table in Subscription
                    plans = frappe.get_all("Subscription Plan Detail", filters={"parent": ref_dn}, fields=["plan"])
                    if plans:
                        # Use the first plan found
                        plan_id = plans[0].plan
                        # Get product_price_id from Subscription Plan
                        variant_id = frappe.db.get_value("Subscription Plan", plan_id, "product_price_id")
                        if variant_id:
                            kwargs["variant_id"] = variant_id
                            frappe.log_error(f"Found Variant ID {variant_id} from Subscription Plan {plan_id}", "LemonSqueezy Debug")
            except Exception as e:
                frappe.log_error(f"Error fetching variant from subscription: {str(e)}", "LemonSqueezy Debug")

        # Check if there's already an active Payment Request for this Sales Order/Invoice
        if kwargs.get("reference_doctype") in ["Sales Order", "Sales Invoice"] and kwargs.get("reference_docname"):
            try:
                existing_pr = frappe.db.get_all(
                    "Payment Request",
                    filters={
                        "reference_doctype": kwargs.get("reference_doctype"),
                        "reference_name": kwargs.get("reference_docname"),
                        "status": ["in", ["Initiated", "Requested"]],  # Only active/pending requests
                        "docstatus": ["<", 2]  # Not cancelled
                    },
                    fields=["name", "grand_total"],
                    order_by="creation desc",
                    limit=1
                )
                
                if existing_pr:
                    payment_request_id = existing_pr[0].name
                    frappe.log_error(
                        f"Found existing Payment Request {payment_request_id} for {kwargs.get('reference_doctype')} {kwargs.get('reference_docname')}",
                        "LemonSqueezy Debug"
                    )
                    # Override the reference to use the existing Payment Request
                    kwargs["reference_doctype"] = "Payment Request"
                    kwargs["reference_docname"] = payment_request_id
            except Exception as e:
                frappe.log_error(f"Error checking for existing Payment Request: {str(e)}", "LemonSqueezy Debug")

        if payment_request_id:
            kwargs["payment_request_id"] = payment_request_id
            
            # Get the amount from Payment Request if not provided or is 0
            amount = kwargs.get("amount")
            if not amount or float(amount) == 0:
                try:
                    pr = frappe.get_doc("Payment Request", payment_request_id)
                    # Try different amount fields in order of preference
                    amount = (
                        pr.get("grand_total") or 
                        pr.get("payment_amount") or 
                        pr.get("total_amount_to_pay") or 
                        0
                    )
                    if amount and float(amount) > 0:
                        kwargs["amount"] = amount
                        frappe.log_error(f"Extracted amount from PR: {amount}", "LemonSqueezy")
                except Exception as e:
                    frappe.log_error(f"Error extracting amount: {str(e)}", "LemonSqueezy")

        # Generate the checkout URL
        # kwargs contains arguments passed from Payment Request
        checkout_url = settings.get_api_checkout_url(**kwargs)

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