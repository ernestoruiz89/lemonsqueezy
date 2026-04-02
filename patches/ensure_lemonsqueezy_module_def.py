import frappe


def execute():
    """Ensure the LemonSqueezy Module Def exists on sites with broken metadata."""
    if frappe.db.exists("Module Def", "LemonSqueezy"):
        return

    doc = frappe.get_doc(
        {
            "doctype": "Module Def",
            "module_name": "LemonSqueezy",
            "app_name": "lemonsqueezy",
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
