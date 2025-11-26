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
	"subscription_unpaused"
]

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
		
	event_name = data.get("met", {}).get("event_name")
	
	# Validate event is supported
	if event_name not in SUPPORTED_EVENTS:
		frappe.log_error(f"Unsupported event type: {event_name}", "LemonSqueezy Webhook")
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
		elif event_name in ["subscription_created", "subscription_updated", "subscription_cancelled", "subscription_resumed", "subscription_expired", "subscription_paused", "subscription_unpaused"]:
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
	custom_data = data.get("meta", {}).get("custom_data", {})
	payment_request_id = custom_data.get("payment_request_id")
	
	# Update Payment Request if exists
	if payment_request_id:
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
		order_data = data.get("data", {})
		attributes = order_data.get("attributes", {})
		
		order_id = str(order_data.get("id"))
		
		# Check if order already exists
		if frappe.db.exists("LemonSqueezy Order", {"order_id": order_id}):
			frappe.log_error(f"Order {order_id} already exists")
			return
		
		# Create new order
		order_doc = frappe.new_doc("LemonSqueezy Order")
		order_doc.order_id = order_id
		order_doc.status = "paid"
		order_doc.customer_email = attributes.get("user_email")
		
		# Amounts (convert from cents to currency)
		order_doc.total = (attributes.get("total") or 0) / 100
		order_doc.subtotal = (attributes.get("subtotal") or 0) / 100
		order_doc.discount_total = (attributes.get("discount_total") or 0) / 100
		order_doc.tax = (attributes.get("tax") or 0) / 100
		order_doc.currency = (attributes.get("currency") or "USD").upper()
		
		# Dates
		order_doc.order_date = get_datetime(attributes.get("created_at"))
		
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
						order_doc.billing_interval = "monthly"
					elif "year" in variant_lower:
						order_doc.billing_interval = "yearly"
					elif "week" in variant_lower:
						order_doc.billing_interval = "weekly"
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
	
	subscription_id = subscription_data.get("id")
	if not subscription_id:
		frappe.log_error("No subscription_id in webhook data")
		return
		
	subscription_id = str(subscription_id)
	customer_id = attributes.get("customer_id")
	product_id = attributes.get("product_id")
	variant_id = attributes.get("variant_id")
	status = attributes.get("status")
	
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
		doc.renews_at = get_datetime(renews_at)
	if ends_at:
		doc.ends_at = get_datetime(ends_at)
	if trial_ends_at:
		doc.trial_ends_at = get_datetime(trial_ends_at)
		
	if update_url:
		doc.update_url = update_url
	if cancel_url:
		doc.cancel_url = cancel_url
	
	# Try to link to a Customer if email matches
	if not doc.customer and user_email:
		customer = frappe.db.get_value("Customer", {"email_id": user_email}, "name")
		if customer:
			doc.customer = customer
			
	doc.save(ignore_permissions=True)
	frappe.db.commit()
