import frappe
from frappe import _

from lemonsqueezy.lemonsqueezy.checkout import (
	resolve_checkout_request_from_token,
	get_legacy_checkout_redirect_url,
)

def get_context(context):
	context.no_cache = 1
	
	token = frappe.form_dict.get("token")
	if not token:
		legacy_redirect_url = get_legacy_checkout_redirect_url(frappe.form_dict)
		if legacy_redirect_url:
			context.redirect_url = legacy_redirect_url
			context.title = _("LemonSqueezy Checkout")
			return
		context.error = _("A valid checkout token is required.")
		context.title = _("LemonSqueezy Checkout")
		return

	try:
		checkout_request = resolve_checkout_request_from_token(token)
		context.reference_doctype = "Payment Request"
		context.reference_docname = checkout_request["payment_request"].name
		context.redirect_url = checkout_request.get("redirect_url")
		if not context.redirect_url:
			context.checkout_url = checkout_request["settings"].get_api_checkout_url(
				**checkout_request["checkout_kwargs"]
			)
	except Exception as e:
		context.error = str(e)
		
	context.title = _("LemonSqueezy Checkout")
