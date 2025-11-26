import frappe
from frappe.model.document import Document
from frappe import _

class LemonSqueezyOrder(Document):
	def validate(self):
		"""Validate order data"""
		if not self.order_id:
			frappe.throw(_("Order ID is required"))
		
		# Normalize currency to uppercase
		if self.currency:
			self.currency = self.currency.upper()
		
		# Link to customer if possible
		if not self.customer and self.customer_email:
			customer = frappe.db.get_value("Customer", {"email_id": self.customer_email}, "name")
			if customer:
				self.customer = customer
		
		# Link to subscription if possible
		if not self.subscription and self.subscription_id:
			subscription = frappe.db.get_value(
				"LemonSqueezy Subscription",
				{"subscription_id": self.subscription_id},
				"name"
			)
			if subscription:
				self.subscription = subscription
	
	def get_monthly_value(self):
		"""Get normalized monthly value for MRR calculation"""
		if not self.is_subscription or self.status != "paid":
			return 0
		
		if not self.billing_interval:
			return 0
		
		interval_count = self.billing_interval_count or 1
		
		if self.billing_interval == "monthly":
			return self.total / interval_count
		elif self.billing_interval == "yearly":
			return (self.total / 12) / interval_count
		elif self.billing_interval == "weekly":
			return (self.total * 4.33) / interval_count  # Average weeks per month
		
		return 0
	
	def get_indicator(self):
		"""Return indicator color based on status"""
		return {
			"paid": "green",
			"pending": "orange",
			"refunded": "red",
			"failed": "red"
		}.get(self.status, "gray")
