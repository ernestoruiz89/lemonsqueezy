import frappe
import hmac
import hashlib
import json
from frappe import _
from frappe.utils import get_datetime

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
    
    # Get event name from metadata
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
    if valid_settings.sanitize_webhook_payload:
        log_payload = sanitize_payload(data)
    
    # Create Webhook Log
    log_doc = frappe.get_doc({
        "doctype": "LemonSqueezy Webhook Log",
        "event_name": event_name,
        "payload": json.dumps(log_payload, indent=2),
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
    
    # Verify order status before processing payment
    order_status = attributes.get("status")
    if order_status != "paid":
        frappe.log_error(
            f"Order status is '{order_status}', not 'paid'. Skipping payment processing.",
            "LemonSqueezy Webhook"
        )
        # Still store the order but don't mark payment as completed
    
    custom_data = data.get("meta", {}).get("custom_data", {})
    payment_request_id = custom_data.get("payment_request_id")
    
    # Update Payment Request if exists AND order is paid
    if payment_request_id and order_status == "paid":
        try:
            if not frappe.db.exists("Payment Request", payment_request_id):
                frappe.log_error(f"Payment Request {payment_request_id} not found")
            else:
                pr = frappe.get_doc("Payment Request", payment_request_id)
                if pr.status != "Paid":
                    pr.status = "Paid"
                    pr.db_set("status", "Paid")
                    pr.run_method("on_payment_authorized", "Completed")
                    frappe.db.commit()
        except Exception as e:
            frappe.log_error(f"Error processing order_created for PR {payment_request_id}: {str(e)}")
    
    # Store order data in LemonSqueezy Order
    try:
        order_id = str(order_data.get("id"))
        
        # Check if order already exists
        if frappe.db.exists("LemonSqueezy Order", {"order_id": order_id}):
            frappe.log_error(f"Order {order_id} already exists")
            return
        
        # Validate email format
        customer_email = attributes.get("user_email")
        if customer_email:
            try:
                frappe.utils.validate_email_address(customer_email, throw=True)
            except frappe.InvalidEmailAddressError:
                frappe.log_error(f"Invalid email format: {customer_email}", "LemonSqueezy Validation")
                customer_email = None  # Don't store invalid email
        
        # Validate currency exists
        currency_code = (attributes.get("currency") or "USD").upper()
        if not frappe.db.exists("Currency", currency_code):
            frappe.log_error(f"Currency {currency_code} not found in system, defaulting to USD", "LemonSqueezy Validation")
            currency_code = "USD"
        
        # Create new order
        order_doc = frappe.new_doc("LemonSqueezy Order")
        order_doc.order_id = order_id
        order_doc.status = "Paid" if order_status == "paid" else "Pending"
        order_doc.customer_email = customer_email
        
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
        currency_code = (attributes.get("currency") or "USD").upper()
        # Validate currency exists
        if frappe.db.exists("Currency", currency_code):
            doc.currency = currency_code
        else:
            frappe.log_error(f"Currency {currency_code} not found, defaulting to USD", "LemonSqueezy Validation")
            doc.currency = "USD"
        
    # Billing Interval - improved detection
    if variant_name:
        variant_lower = variant_name.lower()
        # More specific matching to avoid false positives
        if any(term in variant_lower for term in ["monthly", "per month", "/month", "month"]):
            doc.billing_interval = "Monthly"
        elif any(term in variant_lower for term in ["yearly", "per year", "/year", "annual"]):
            doc.billing_interval = "Yearly"
        elif any(term in variant_lower for term in ["weekly", "per week", "/week"]):
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
							debug_log(settings, f"Found Variant ID {item_variant_id} from Item {item_code}")
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
										debug_log(settings, f"Found Variant ID {variant_id} from Invoice Item Plan {plan_id}")

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
							debug_log(settings, f"Found Variant ID {variant_id} from Subscription Plan {plan_id}")
			except Exception as e:
				debug_log(settings, f"Error fetching variant from subscription: {str(e)}")

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
