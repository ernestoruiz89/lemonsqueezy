import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
	"""Add LemonSqueezy Variant ID field to Item DocType"""
	
	custom_fields = {
		"Item": [
			{
				"fieldname": "lemonsqueezy_variant_id",
				"label": "LemonSqueezy Variant ID",
				"fieldtype": "Data",
				"insert_after": "max_discount",
				"description": "LemonSqueezy Variant ID for this specific product. Leave blank to use default variant with custom price.",
				"translatable": 0,
				"read_only": 0,
				"print_hide": 1,
				"no_copy": 0
			}
		]
	}
	
	create_custom_fields(custom_fields, update=True)
	frappe.db.commit()
