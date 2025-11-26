# LemonSqueezy Integration for Frappe/ERPNext

**LemonSqueezy** payment gateway integration for Frappe/ERPNext. Allows processing payments, managing subscriptions, and receiving webhooks from LemonSqueezy.

## 📋 Features

- ✅ Payment processing through LemonSqueezy
- ✅ Subscription management
- ✅ Webhooks for payment and subscription events
- ✅ Support for custom pricing
- ✅ Integration with Frappe Payment Requests
- ✅ Webhook log recording
- ✅ HMAC signature validation

---

## 🚀 Installation

### 1. Install the app

```bash
# Navigate to the bench directory
cd frappe-bench

# Get the app from GitHub
bench get-app https://github.com/ernestoruiz89/lemonsqueezy.git

# Install the app on your site (skip assets since this app has no frontend JS/CSS)
bench --site [site-name] install-app lemonsqueezy --skip-assets

# Migrate
bench --site [site-name] migrate
```

### 2. Restart bench

```bash
bench restart
```

---

## ⚙️ Configuration

### 1. Get LemonSqueezy Credentials

1. Log in to [LemonSqueezy](https://lemonsqueezy.com)
2. Go to **Settings** → **API**
3. Create a new **API Key**
4. Copy your **Store ID** (you can find it in the store section)

### 2. Configure Webhook Secret

1. In LemonSqueezy, go to **Settings** → **Webhooks**
2. Create a new webhook pointing to:
   ```
   https://your-site.com/api/method/lemonsqueezy.lemonsqueezy.api.handle_webhook
   ```
3. Select the events you want to receive:
   - `order_created`
   - `subscription_created`
   - `subscription_updated`
   - `subscription_cancelled`
   - `subscription_resumed`
   - `subscription_expired`
   - `subscription_paused`
   - `subscription_unpaused`
4. Copy the **Signing Secret**

### 3. Configure in Frappe

1. Go to **LemonSqueezy Settings**
2. Create a new document:
   - **Enabled**: ☑
   - **Gateway Name**: `Standard` (or your preferred name)
   - **API Key**: Paste your API key
   - **Store ID**: Paste your Store ID
   - **Webhook Secret**: Paste the webhook signing secret
   - **Default Variant ID** (optional): Variant ID for generic payments
3. Save the document

---

## 📖 Usage

### Create a Checkout for Payment Request

```python
import frappe

# Get the Payment Request
pr = frappe.get_doc("Payment Request", "PR-00001")

# Generate checkout URL
gateway_controller = frappe.get_doc("LemonSqueezy Settings", "LemonSqueezy-Standard")

checkout_url = gateway_controller.get_api_checkout_url(
    amount=pr.grand_total,
    currency=pr.currency,
    payer_email=pr.email_to,
    payer_name="John Doe",
    reference_doctype="Payment Request",
    reference_docname=pr.name,
    order_id=pr.name,
    variant_id="123456"  # Optional
)

print(checkout_url)
```

### Check Subscription Status

```python
import frappe

# Get subscription
subscription = frappe.get_doc("LemonSqueezy Subscription", "SUB-12345")

# Check if it's active
if subscription.is_active():
    print("Active subscription")

# Get customer portal URL
portal_url = subscription.get_portal_url()
print(f"Portal: {portal_url}")

# Get status color
color = subscription.get_status_color()
print(f"Indicator color: {color}")
```

### Whitelisted API

Get customer portal URL from frontend:

```javascript
frappe.call({
    method: "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.get_customer_portal_url_api",
    args: {
        subscription_id: "12345"
    },
    callback: function(r) {
        if (r.message) {
            window.open(r.message, '_blank');
        }
    }
});
```

---

## 🔔 Webhooks

Webhooks are automatically processed and logged in **LemonSqueezy Webhook Log**.

### Supported Events

| Event | Description |
|--------|-------------|
| `order_created` | An order is created (updates Payment Request to "Paid") |
| `subscription_created` | A subscription is created |
| `subscription_updated` | A subscription is updated |
| `subscription_cancelled` | A subscription is cancelled |
| `subscription_resumed` | A subscription is resumed |
| `subscription_expired` | A subscription expires |
| `subscription_paused` | A subscription is paused |
| `subscription_unpaused` | A paused subscription is resumed |

### Subscription States

- `active` - Active
- `on_trial` - On trial period
- `paused` - Paused
- `past_due` - Past due payment
- `unpaid` - Unpaid
- `cancelled` - Cancelled
- `expired` - Expired

---

## 🔧 Troubleshooting

### Webhook not received

1. Verify that the webhook URL is publicly accessible
2. Check **Error Log** in Frappe to see errors
3. Verify that the webhook is enabled in LemonSqueezy
4. Make sure the **Signing Secret** is correct

### Error: "Invalid signature"

- Verify that the **Webhook Secret** in Settings is correct
- Make sure to copy the complete secret without spaces

### Checkout URL not generated

1. Verify that the **API Key** and **Store ID** are correct
2. Make sure the **Variant ID** exists in your store
3. If using custom pricing, verify that the variant allows it
4. Check **Error Log** for more details

### Payment Request not marked as "Paid"

1. Verify that the `order_created` webhook is configured
2. Make sure to pass `order_id` or `payment_request_id` in the checkout
3. Check **LemonSqueezy Webhook Log** to see if the event was received

---

## 📚 DocTypes Structure

### LemonSqueezy Settings
Payment gateway configuration.

### LemonSqueezy Subscription
Stores subscription information synchronized from LemonSqueezy.

### LemonSqueezy Webhook Log
Log of all received webhooks with their payload and status.

---

## 🔐 Security

- ✅ HMAC signature validation on webhooks
- ✅ Secure API key storage (Password fields)
- ✅ Error logging without exposing sensitive data
- ⚠️ **Recommendation**: Implement rate limiting in production

---

## 🛠️ Development

### Run Tests

```bash
# Unit tests (when available)
bench --site [site-name] run-tests --app lemonsqueezy
```

### Logs

Review application logs:

```bash
# Error logs
bench --site [site-name] console
>>> frappe.get_all("Error Log", filters={"error": ["like", "%LemonSqueezy%"]}, limit=10)

# Webhook logs
>>> frappe.get_all("LemonSqueezy Webhook Log", limit=10)
```

---

## 📞 Support

- **LemonSqueezy Documentation**: https://docs.lemonsqueezy.com
- **API Reference**: https://docs.lemonsqueezy.com/api
- **Frappe Framework**: https://frappeframework.com/docs

---

## 📄 License

MIT License

---

## 🤝 Contributions

Contributions are welcome. Please:

1. Fork the project
2. Create a branch for your feature (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📝 Changelog

### v0.0.1 (Initial Release)
- Basic integration with LemonSqueezy API
- Payment and subscription processing
- Webhook handling
- Payment Request integration
