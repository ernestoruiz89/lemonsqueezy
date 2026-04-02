import frappe


def execute():
    """Enable webhook payload sanitization for existing LemonSqueezy Settings."""
    settings_names = frappe.get_all("LemonSqueezy Settings", pluck="name")
    if not settings_names:
        return

    updated = False
    for name in settings_names:
        doc = frappe.get_doc("LemonSqueezy Settings", name)
        if not doc.sanitize_webhook_payload:
            doc.db_set("sanitize_webhook_payload", 1, update_modified=False)
            updated = True

    if updated:
        frappe.db.commit()
