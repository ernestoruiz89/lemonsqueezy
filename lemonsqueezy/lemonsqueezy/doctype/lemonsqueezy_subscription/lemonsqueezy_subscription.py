import frappe
from frappe.model.document import Document
from frappe import _

class LemonSqueezySubscription(Document):
	def get_portal_url(self):
		"""Get the customer portal URL for this subscription"""
		if self.cancel_url:
			return self.cancel_url
		
		# Fallback: try to fetch from API
		try:
			settings = frappe.get_all("LemonSqueezy Settings", filters={"enabled": 1}, limit=1)
			if settings:
				settings_doc = frappe.get_doc("LemonSqueezy Settings", settings[0].name)
				return settings_doc.get_customer_portal_url(self.subscription_id)
		except Exception as e:
			frappe.log_error(f"Error getting portal URL: {str(e)}")
		
		return None
	
	def get_status_color(self):
		"""Return indicator color based on subscription status"""
		status_colors = {
			"active": "green",
			"on_trial": "blue",
			"paused": "orange",
			"past_due": "orange",
			"unpaid": "red",
			"cancelled": "red",
			"expired": "gray"
		}
		return status_colors.get(self.status, "gray")
	
	def is_active(self):
		"""Check if subscription is active"""
		return self.status in ["active", "on_trial"]
	
	def validate(self):
		"""Validate subscription data"""
		if not self.subscription_id:
			frappe.throw(_("Subscription ID is required"))
		
		if not self.status:
			frappe.throw(_("Status is required"))
