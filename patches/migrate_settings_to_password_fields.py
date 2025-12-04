import frappe

def execute():
    """
    Migrate api_key and webhook_secret from Small Text to Password fieldtype.
    
    This patch reads the existing values from the table columns and stores them
    in the encrypted __Auth table using set_password().
    """
    
    # Get all LemonSqueezy Settings documents with credentials
    settings_list = frappe.db.sql("""
        SELECT name, api_key, webhook_secret 
        FROM `tabLemonSqueezy Settings`
        WHERE api_key IS NOT NULL OR webhook_secret IS NOT NULL
    """, as_dict=True)
    
    if not settings_list:
        return
    
    for settings in settings_list:
        try:
            doc = frappe.get_doc("LemonSqueezy Settings", settings.name)
            
            # Migrate api_key if it exists
            if settings.api_key and settings.api_key not in ["", "***"]:
                try:
                    existing = doc.get_password("api_key")
                except:
                    existing = None
                
                if not existing:
                    doc.api_key = settings.api_key
                    doc.save(ignore_permissions=True)
            
            # Migrate webhook_secret if it exists
            if settings.webhook_secret and settings.webhook_secret not in ["", "***"]:
                try:
                    existing = doc.get_password("webhook_secret")
                except:
                    existing = None
                
                if not existing:
                    doc.webhook_secret = settings.webhook_secret
                    doc.save(ignore_permissions=True)
            
        except Exception as e:
            frappe.log_error(f"Error migrating {settings.name}: {str(e)}", "LemonSqueezy Patch")
    
    frappe.db.commit()
