import frappe
from frappe.model.document import Document
from frappe import _
from payments.utils import create_payment_gateway
from frappe.utils import get_url, cint, flt
from urllib.parse import urlencode
import requests

class LemonSqueezySettings(Document):
	def validate(self):
		"""Validate settings before saving"""
		if self.api_key and self.store_id:
			self.validate_credentials()
	
	def validate_credentials(self):
		"""Validate LemonSqueezy API credentials"""
		if self.flags.ignore_mandatory:
			return
			
		api_key = self.get_password("api_key")
		headers = {
			"Authorization": f"Bearer {api_key}",
			"Accept": "application/vnd.api+json"
		}
		
		try:
			# Test API connection by fetching store info
			response = requests.get(
				f"https://api.lemonsqueezy.com/v1/stores/{self.store_id}",
				headers=headers,
				timeout=10
			)
			response.raise_for_status()
		except requests.exceptions.Timeout:
			frappe.throw(_("Connection timeout. Please check your internet connection."))
		except requests.exceptions.HTTPError as e:
			if e.response.status_code == 401:
				frappe.throw(_("Invalid API Key. Please check your credentials."))
			elif e.response.status_code == 404:
				frappe.throw(_("Store ID not found. Please check your Store ID."))
			else:
				frappe.throw(_("API Error: {0}").format(str(e)))
		except Exception as e:
			frappe.log_error(f"LemonSqueezy credential validation error: {str(e)}")
			frappe.throw(_("Failed to validate credentials: {0}").format(str(e)))

	def on_update(self):
		create_payment_gateway(
			"LemonSqueezy-" + self.gateway_name,
			settings="LemonSqueezy Settings",
			controller=self.gateway_name,
		)
		frappe.db.commit()

	def get_payment_url(self, **kwargs):
		"""
		Returns the URL to the local checkout page.
		"""
		return get_url(f"./lemonsqueezy_checkout?{urlencode(kwargs)}")

	def get_api_checkout_url(self, **kwargs):
		"""
		Generates a LemonSqueezy Checkout URL via API.
		kwargs should contain:
		- amount: (optional) Amount to charge (if variant supports custom price)
		- currency: (optional) Currency code
		- payer_email: (optional)
		- payer_name: (optional)
		- reference_doctype: (optional)
		- reference_docname: (optional)
		- variant_id: (optional) Specific variant to purchase
		"""
		
		api_key = self.get_password("api_key")
		store_id = self.store_id
		variant_id = kwargs.get("variant_id") or self.default_variant_id
		
		if not variant_id:
			frappe.throw(_("Variant ID is required for LemonSqueezy payment. Please set a Default Variant ID in settings or provide one."))
			
		headers = {
			"Authorization": f"Bearer {api_key}",
			"Accept": "application/vnd.api+json",
			"Content-Type": "application/vnd.api+json"
		}
		
		# Construct payload
		payload = {
			"data": {
				"type": "checkouts",
				"attributes": {
					"checkout_data": {
						"custom": {
							"reference_doctype": kwargs.get("reference_doctype"),
							"reference_docname": kwargs.get("reference_docname"),
							"payment_request_id": kwargs.get("order_id")
						}
					}
				},
				"relationships": {
					"store": {
						"data": {
							"type": "stores",
							"id": str(store_id)
						}
					},
					"variant": {
						"data": {
							"type": "variants",
							"id": str(variant_id)
						}
					}
				}
			}
		}
		
		# Add email and name if provided
		if kwargs.get("payer_email"):
			payload["data"]["attributes"]["checkout_data"]["email"] = kwargs.get("payer_email")
		if kwargs.get("payer_name"):
			payload["data"]["attributes"]["checkout_data"]["name"] = kwargs.get("payer_name")
		
		# Handle custom price if amount is provided
		# Note: The variant must be configured to allow "Pay what you want" or custom price in LemonSqueezy dashboard
		amount = kwargs.get("amount")
		if amount:
			# LemonSqueezy expects amount in cents (integer)
			# Use cint and flt for proper conversion
			amount_in_cents = cint(flt(amount) * 100)
			payload["data"]["attributes"]["checkout_data"]["custom_price"] = amount_in_cents
			
		try:
			response = requests.post(
				"https://api.lemonsqueezy.com/v1/checkouts",
				json=payload,
				headers=headers,
				timeout=10
			)
			response.raise_for_status()
			data = response.json()
			return data["data"]["attributes"]["url"]
		except requests.exceptions.Timeout:
			frappe.log_error("LemonSqueezy API Timeout")
			frappe.throw(_("Request timeout. Please try again."))
		except requests.exceptions.HTTPError as e:
			error_detail = e.response.text if hasattr(e.response, 'text') else str(e)
			frappe.log_error(f"LemonSqueezy API Error: {error_detail}")
			frappe.throw(_("Failed to create LemonSqueezy checkout: {0}").format(str(e)))
		except Exception as e:
			frappe.log_error(f"LemonSqueezy Error: {str(e)}")
			frappe.throw(_("Failed to create LemonSqueezy checkout: {0}").format(str(e)))

	def get_customer_portal_url(self, subscription_id):
		"""
		Get the Customer Portal URL for a specific subscription.
		"""
		api_key = self.get_password("api_key")
		headers = {
			"Authorization": f"Bearer {api_key}",
			"Accept": "application/vnd.api+json"
		}

		try:
			response = requests.get(
				f"https://api.lemonsqueezy.com/v1/subscriptions/{subscription_id}",
				headers=headers,
				timeout=10
			)
			response.raise_for_status()
			data = response.json()
			return data["data"]["attributes"]["urls"]["customer_portal"]
		except Exception as e:
			frappe.log_error(f"LemonSqueezy Error: {str(e)}")
			frappe.throw(_("Failed to get Customer Portal URL: {0}").format(str(e)))

@frappe.whitelist()
def get_customer_portal_url_api(subscription_id):
	"""
	Whitelist method to get customer portal URL
	Can be called from frontend
	"""
	# Get any LemonSqueezy Settings (or pass specific one)
	settings = frappe.get_all("LemonSqueezy Settings", limit=1)
	if not settings:
		frappe.throw(_("No LemonSqueezy Settings found"))
	
	doc = frappe.get_doc("LemonSqueezy Settings", settings[0].name)
	return doc.get_customer_portal_url(subscription_id)

@frappe.whitelist()
def test_connection(name):
	"""
	Test LemonSqueezy API connection
	"""
	doc = frappe.get_doc("LemonSqueezy Settings", name)
	try:
		doc.validate_credentials()
		return {"success": True, "message": "Connection successful"}
	except Exception as e:
		frappe.throw(str(e))
