import frappe


def execute():
    """Regenerate active LemonSqueezy payment links using the current controller."""
    if not frappe.db.exists("DocType", "Payment Request"):
        return

    if not frappe.db.has_column("Payment Request", "payment_url"):
        return

    payment_requests = frappe.get_all(
        "Payment Request",
        filters={
            "payment_gateway": "LemonSqueezy",
            "status": ["not in", ["Paid", "Cancelled"]],
        },
        pluck="name",
    )

    for payment_request_name in payment_requests:
        try:
            payment_request = frappe.get_doc("Payment Request", payment_request_name)
            payment_url = payment_request.get_payment_url()
            if payment_url and payment_url != payment_request.payment_url:
                payment_request.db_set("payment_url", payment_url, update_modified=False)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LemonSqueezy: failed to refresh payment URL for {payment_request_name}",
            )
