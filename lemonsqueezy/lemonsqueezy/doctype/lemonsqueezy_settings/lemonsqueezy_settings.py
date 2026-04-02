import frappe
import requests
from frappe.model.document import Document
from frappe import _
from frappe.utils import cint, flt

from lemonsqueezy.lemonsqueezy.checkout import get_checkout_redirect_url, issue_checkout_token

# LemonSqueezy supported currencies
SUPPORTED_CURRENCIES = [
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BIF", "BMD", "BND", "BOB", "BRL", "BSD",
    "BWP", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY", "COP", "CRC", "CVE",
    "CZK", "DJF", "DKK", "DOP", "DZD", "EGP", "ETB", "EUR", "FJD", "FKP",
    "GBP", "GEL", "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG",
    "HUF", "IDR", "ILS", "INR", "ISK", "JMD", "JPY", "KES", "KGS", "KHR",
    "KMF", "KRW", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL", "MAD",
    "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MUR", "MVR", "MWK", "MXN",
    "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR", "NZD", "PAB", "PEN",
    "PGK", "PHP", "PKR", "PLN", "PYG", "QAR", "RON", "RSD", "RWF", "SAR",
    "SBD", "SCR", "SEK", "SGD", "SHP", "SLL", "SOS", "SRD", "SZL", "THB",
    "TJS", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD", "UYU",
    "UZS", "VND", "VUV", "WST", "XAF", "XCD", "XOF", "XPF", "YER", "ZAR", "ZMW"
]


def _get_json_api_error_detail(response):
    try:
        payload = response.json()
    except Exception:
        return None

    errors = payload.get("errors") or []
    if not errors:
        return None

    detail = errors[0].get("detail") or errors[0].get("title")
    return str(detail).strip() if detail else None


def _resource_exists(url, headers):
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except Exception:
        return None

    if response.status_code == 404:
        return False
    if 200 <= response.status_code < 300:
        return True
    return None


def _build_checkout_not_found_message(store_id, variant_id, headers):
    store_exists = _resource_exists(
        f"https://api.lemonsqueezy.com/v1/stores/{store_id}",
        headers,
    )
    variant_exists = _resource_exists(
        f"https://api.lemonsqueezy.com/v1/variants/{variant_id}",
        headers,
    )

    if store_exists is False:
        return _(
            "Store ID {0} was not found for this API key. Verify the Store ID and make sure the API key is in the same mode (test/live) as the store."
        ).format(store_id)

    if variant_exists is False:
        return _(
            "Variant ID {0} was not found for this API key. Verify that you configured a LemonSqueezy Variant ID, not a Price ID, and make sure the API key is in the same mode (test/live) as the variant."
        ).format(variant_id)

    return _(
        "LemonSqueezy could not create the checkout for Store ID {0} and Variant ID {1}. Verify that the variant belongs to the configured store and that all resources use the same mode (test/live)."
    ).format(store_id, variant_id)


class LemonSqueezySettings(Document):
    # Define supported currencies for payment gateway integration
    supported_currencies = SUPPORTED_CURRENCIES
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

        # Validate Default Variant ID if set
        if self.default_variant_id:
            try:
                response = requests.get(
                    f"https://api.lemonsqueezy.com/v1/variants/{self.default_variant_id}",
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 404:
                    frappe.throw(_("Default Variant ID {0} does not exist in LemonSqueezy.").format(self.default_variant_id))
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    frappe.throw(_("Default Variant ID {0} does not exist in LemonSqueezy.").format(self.default_variant_id))
                frappe.msgprint(_("Warning: Could not validate Variant ID: {0}").format(str(e)))
            except Exception as e:
                frappe.msgprint(_("Warning: Could not validate Variant ID: {0}").format(str(e)))

    def validate_transaction_currency(self, currency):
        """Validate transaction currency against LemonSqueezy supported currencies"""
        if currency:
            currency = currency.upper()
            if currency not in SUPPORTED_CURRENCIES:
                frappe.throw(
                    _("Currency {0} is not supported by LemonSqueezy. Supported currencies: {1}").format(
                        currency, ", ".join(SUPPORTED_CURRENCIES[:10]) + "..."
                    )
                )

    def get_payment_url(self, **kwargs):
        """
        Returns the URL to the local checkout page.
        """
        try:
            if kwargs.get("reference_doctype") != "Payment Request" or not kwargs.get("reference_docname"):
                frappe.throw(
                    _("LemonSqueezy checkout links can only be generated from a Payment Request.")
                )

            payment_request_name = kwargs.get("reference_docname")
            if not frappe.db.exists("Payment Request", payment_request_name):
                frappe.throw(_("Payment Request {0} was not found.").format(payment_request_name))

            token = issue_checkout_token(payment_request_name, self.name)
            return get_checkout_redirect_url(token)
        except Exception as e:
            frappe.log_error(f"Error generating payment URL: {str(e)}", "LemonSqueezy Error")
            return None

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
        
        # Construct minimal payload
        payload = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "custom_price": None  # Will be set if amount is provided
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

        # Prepare checkout metadata and buyer details
        checkout_data = {}

        if kwargs.get("payer_email"):
            checkout_data["email"] = kwargs.get("payer_email")

        if kwargs.get("payer_name"):
            checkout_data["name"] = kwargs.get("payer_name")

        # Attach custom metadata so the webhook can correlate payments to ERPNext documents
        custom_data = {
            key: kwargs[key]
            for key in ("payment_request_id", "reference_doctype", "reference_docname")
            if kwargs.get(key)
        }

        if custom_data:
            checkout_data["custom"] = custom_data

        if checkout_data:
            payload["data"]["attributes"]["checkout_data"] = checkout_data

        # Handle custom price if amount is provided
        amount = kwargs.get("amount")
        if amount:
            # LemonSqueezy expects amount in cents (integer)
            amount_in_cents = cint(flt(amount) * 100)
            payload["data"]["attributes"]["custom_price"] = amount_in_cents
        else:
            # Remove custom_price if not needed
            del payload["data"]["attributes"]["custom_price"]

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
            status_code = getattr(e.response, "status_code", "unknown")
            request_id = getattr(e.response, "headers", {}).get("X-Request-Id", "n/a") if getattr(e, "response", None) else "n/a"
            detail = _get_json_api_error_detail(e.response) if getattr(e, "response", None) else None
            message = (
                f"LemonSqueezy API Error: status={status_code}, request_id={request_id}, "
                f"store_id={store_id}, variant_id={variant_id}, reference={kwargs.get('reference_doctype')}:{kwargs.get('reference_docname')}"
            )
            if detail:
                message += f", detail={detail[:300]}"
            frappe.log_error(
                message=message,
                title="LemonSqueezy API Error",
            )
            if status_code == 404:
                frappe.throw(_build_checkout_not_found_message(store_id, variant_id, headers))
            frappe.throw(_("Failed to create LemonSqueezy checkout. Please check Error Log for details."))
        except Exception as e:
            frappe.log_error(
                message=f"LemonSqueezy Checkout Error: {type(e).__name__}",
                title="LemonSqueezy Checkout Error",
            )
            frappe.throw(_("Failed to create LemonSqueezy checkout. Please check Error Log."))

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

def _normalize_email(value):
    """Normalize user-facing email values before comparing ownership."""
    return (value or "").strip().lower()

def _get_customer_names_for_user(user_email):
    """Resolve every Customer linked to the given user email."""
    normalized_email = _normalize_email(user_email)
    if not normalized_email:
        return set()

    customers = {
        row.name
        for row in frappe.get_all("Customer", filters={"email_id": normalized_email}, fields=["name"])
        if row.name
    }

    linked_customers = frappe.db.sql(
        """
        SELECT DISTINCT dl.link_name
        FROM `tabContact` c
        JOIN `tabContact Email` ce ON ce.parent = c.name
        JOIN `tabDynamic Link` dl ON dl.parent = c.name
        WHERE ce.email_id = %s
        AND dl.link_doctype = 'Customer'
        """,
        (normalized_email,),
        as_dict=1,
    )

    customers.update(row.link_name for row in linked_customers if row.link_name)
    return customers

def _can_access_customer_portal(subscription, user=None):
    """Allow admins or the user/customer that owns the subscription."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return False

    if "System Manager" in frappe.get_roles(user):
        return True

    if frappe.has_permission("LemonSqueezy Subscription", doc=subscription, ptype="read", user=user):
        return True

    user_email = _normalize_email(frappe.db.get_value("User", user, "email") or user)
    if not user_email:
        return False

    if _normalize_email(getattr(subscription, "customer_email", None)) == user_email:
        return True

    subscription_customer = getattr(subscription, "customer", None)
    if subscription_customer and subscription_customer in _get_customer_names_for_user(user_email):
        return True

    return False

@frappe.whitelist()
def get_customer_portal_url_api(subscription_id):
    """
    Whitelist method to get customer portal URL
    Can be called from frontend
    """
    subscription_id = str(subscription_id or "").strip()
    if not subscription_id:
        frappe.throw(_("Subscription ID is required"))

    user = frappe.session.user
    is_system_manager = bool(user and user != "Guest" and "System Manager" in frappe.get_roles(user))
    subscription_name = frappe.db.get_value(
        "LemonSqueezy Subscription",
        {"subscription_id": subscription_id},
        "name",
    )

    if not subscription_name:
        if is_system_manager:
            frappe.throw(_("Subscription {0} was not found.").format(subscription_id))
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    subscription = frappe.get_doc("LemonSqueezy Subscription", subscription_name)
    if not _can_access_customer_portal(subscription, user=user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    # Get any enabled LemonSqueezy Settings
    settings = frappe.get_all("LemonSqueezy Settings", filters={"enabled": 1}, limit=1)
    if not settings:
        frappe.throw(_("No enabled LemonSqueezy Settings found"))
    
    doc = frappe.get_doc("LemonSqueezy Settings", settings[0].name)
    return doc.get_customer_portal_url(subscription_id)

@frappe.whitelist()
def test_connection(name):
    """
    Test LemonSqueezy API connection
    Called from Settings page
    """
    try:
        doc = frappe.get_doc("LemonSqueezy Settings", name)
        doc.validate_credentials()
        return {"success": True, "message": _("Connection successful!")}
    except Exception as e:
        return {"success": False, "message": str(e)}
