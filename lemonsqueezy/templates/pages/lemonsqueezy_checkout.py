import frappe
from frappe import _
from payments.utils import get_payment_gateway_controller

def get_context(context):
	context.no_cache = 1
	
	# Get parameters from request
	params = frappe.form_dict
	
	if not params.get("payment_gateway"):
		frappe.throw(_("Payment Gateway is required"))
		
	# Get the controller (LemonSqueezy Settings)
	controller = get_payment_gateway_controller(params.get("payment_gateway"))
	
	# Pass reference docs to context for redirect
	context.reference_doctype = params.get("reference_doctype")
	context.reference_docname = params.get("reference_docname")
	
	# Generate the API checkout URL
	try:
		checkout_url = controller.get_api_checkout_url(**params)
		context.checkout_url = checkout_url
	except Exception as e:
		context.error = str(e)
		
	context.title = _("LemonSqueezy Checkout")
