import frappe
from frappe import _
from frappe.utils import today, add_months, get_first_day, get_last_day, getdate, flt
from datetime import datetime, timedelta

@frappe.whitelist()
def get_dashboard_data():
	"""Get all dashboard metrics in one call for better performance"""
	return {
		"active_subscriptions": get_active_subscriptions_count(),
		"new_subscriptions": get_new_subscriptions_this_month(),
		"churn_rate": get_churn_rate(),
		"subscriptions_trend": get_subscriptions_trend(),
		"distribution": get_subscription_distribution(),
		"top_products": get_top_products(),
		# Revenue metrics
		"mrr": get_mrr(),
		"total_revenue": get_total_revenue(),
		"revenue_this_month": get_revenue_this_month(),
		"avg_order_value": get_average_order_value()
	}

@frappe.whitelist()
def get_active_subscriptions_count():
	"""Get count of active subscriptions (active + on_trial)"""
	count = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={"status": ["in", ["active", "on_trial"]]}
	)
	
	# Get previous month count for comparison
	first_day_this_month = get_first_day(today())
	prev_count = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"status": ["in", ["active", "on_trial"]],
			"modified": ["<", first_day_this_month]
		}
	)
	
	# Calculate percentage change
	if prev_count > 0:
		change_pct = ((count - prev_count) / prev_count) * 100
	else:
		change_pct = 100 if count > 0 else 0
	
	return {
		"value": count,
		"change_percent": round(change_pct, 1),
		"previous_value": prev_count
	}

@frappe.whitelist()
def get_new_subscriptions_this_month():
	"""Get count of new subscriptions created this month"""
	first_day = get_first_day(today())
	last_day = get_last_day(today())
	
	count = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"creation": ["between", [first_day, last_day]]
		}
	)
	
	# Get previous month for comparison
	prev_month_first = get_first_day(add_months(today(), -1))
	prev_month_last = get_last_day(add_months(today(), -1))
	
	prev_count = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"creation": ["between", [prev_month_first, prev_month_last]]
		}
	)
	
	# Calculate percentage change
	if prev_count > 0:
		change_pct = ((count - prev_count) / prev_count) * 100
	else:
		change_pct = 100 if count > 0 else 0
	
	return {
		"value": count,
		"change_percent": round(change_pct, 1),
		"previous_value": prev_count
	}

@frappe.whitelist()
def get_churn_rate():
	"""Calculate monthly churn rate (cancelled/expired vs active subscriptions)"""
	# Get cancelled/expired this month
	first_day = get_first_day(today())
	last_day = get_last_day(today())
	
	churned = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"status": ["in", ["cancelled", "expired"]],
			"modified": ["between", [first_day, last_day]]
		}
	)
	
	# Get active subscriptions at start of month
	active_start = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"status": ["in", ["active", "on_trial"]],
			"creation": ["<", first_day]
		}
	)
	
	# Calculate churn rate as percentage
	if active_start > 0:
		churn_rate = (churned / active_start) * 100
	else:
		churn_rate = 0
	
	# Get previous month churn for comparison
	prev_month_first = get_first_day(add_months(today(), -1))
	prev_month_last = get_last_day(add_months(today(), -1))
	
	prev_churned = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"status": ["in", ["cancelled", "expired"]],
			"modified": ["between", [prev_month_first, prev_month_last]]
		}
	)
	
	prev_active = frappe.db.count(
		"LemonSqueezy Subscription",
		filters={
			"status": ["in", ["active", "on_trial"]],
			"creation": ["<", prev_month_first]
		}
	)
	
	prev_churn_rate = (prev_churned / prev_active * 100) if prev_active > 0 else 0
	
	return {
		"value": round(churn_rate, 2),
		"previous_value": round(prev_churn_rate, 2),
		"churned_count": churned,
		"active_count": active_start
	}

@frappe.whitelist()
def get_subscriptions_trend(months=6):
	"""Get subscription trends for the last N months"""
	months = int(months)
	trend_data = []
	
	for i in range(months - 1, -1, -1):
		month_date = add_months(today(), -i)
		first_day = get_first_day(month_date)
		last_day = get_last_day(month_date)
		
		# New subscriptions this month
		new_subs = frappe.db.count(
			"LemonSqueezy Subscription",
			filters={"creation": ["between", [first_day, last_day]]}
		)
		
		# Active at end of month
		active_subs = frappe.db.count(
			"LemonSqueezy Subscription",
			filters={
				"status": ["in", ["active", "on_trial"]],
				"creation": ["<=", last_day]
			}
		)
		
		# Cancelled this month
		cancelled_subs = frappe.db.count(
			"LemonSqueezy Subscription",
			filters={
				"status": ["in", ["cancelled", "expired"]],
				"modified": ["between", [first_day, last_day]]
			}
		)
		
		trend_data.append({
			"month": getdate(month_date).strftime("%b %Y"),
			"new": new_subs,
			"active": active_subs,
			"cancelled": cancelled_subs
		})
	
	return trend_data

@frappe.whitelist()
def get_subscription_distribution():
	"""Get distribution of subscriptions by status"""
	statuses = frappe.db.get_all(
		"LemonSqueezy Subscription",
		fields=["status", "count(*) as count"],
		group_by="status"
	)
	
	# Format for chart
	distribution = []
	for status in statuses:
		distribution.append({
			"name": status.status.replace("_", " ").title(),
			"value": status.count
		})
	
	return distribution

@frappe.whitelist()
def get_top_products(limit=5):
	"""Get top products/variants by subscription count"""
	limit = int(limit)
	
	products = frappe.db.get_all(
		"LemonSqueezy Subscription",
		fields=[
			"product_name",
			"variant_name",
			"count(*) as count"
		],
		filters={"status": ["in", ["active", "on_trial"]]},
		group_by="product_name, variant_name",
		order_by="count desc",
		limit=limit
	)
	
	# Format for display
	top_products = []
	for product in products:
		name = product.product_name or "Unknown Product"
		if product.variant_name:
			name += f" - {product.variant_name}"
		
		top_products.append({
			"name": name,
			"count": product.count
		})
	
	return top_products

@frappe.whitelist()
def get_expiring_soon(days=30):
	"""Get subscriptions expiring in the next N days"""
	days = int(days)
	threshold_date = add_months(today(), 0)
	end_date = getdate(threshold_date) + timedelta(days=days)
	
	expiring = frappe.db.get_all(
		"LemonSqueezy Subscription",
		fields=["name", "customer_email", "product_name", "ends_at"],
		filters={
			"status": ["in", ["active", "on_trial"]],
			"ends_at": ["between", [today(), end_date]]
		},
		order_by="ends_at asc",
		limit=10
	)
	
	return expiring

# ========== REVENUE METRICS ==========

@frappe.whitelist()
def get_mrr():
	"""Calculate Monthly Recurring Revenue (MRR)"""
	# Get all active subscription orders
	active_subs = frappe.get_all(
		"LemonSqueezy Subscription",
		filters={"status": ["in", ["active", "on_trial"]]},
		fields=["subscription_id"]
	)
	
	total_mrr = 0
	
	for sub in active_subs:
		# Get most recent order for this subscription
		orders = frappe.get_all(
			"LemonSqueezy Order",
			filters={
				"subscription_id": sub.subscription_id,
				"status": "paid",
				"is_subscription": 1
			},
			fields=["name"],
			order_by="order_date desc",
			limit=1
		)
		
		if orders:
			order = frappe.get_doc("LemonSqueezy Order", orders[0].name)
			monthly_value = order.get_monthly_value()
			total_mrr += monthly_value
	
	# Get previous month MRR for comparison (simplified)
	# In production, you'd want to cache this daily
	prev_mrr = total_mrr * 0.95  # Placeholder - implement proper historical tracking
	
	if prev_mrr > 0:
		change_pct = ((total_mrr - prev_mrr) / prev_mrr) * 100
	else:
		change_pct = 100 if total_mrr > 0 else 0
	
	return {
		"value": round(total_mrr, 2),
		"change_percent": round(change_pct, 1),
		"previous_value": round(prev_mrr, 2)
	}

@frappe.whitelist()
def get_total_revenue():
	"""Get total lifetime revenue"""
	result = frappe.db.sql("""
		SELECT SUM(total) as total
		FROM `tabLemonSqueezy Order`
		WHERE status = 'paid'
	""", as_dict=1)
	
	total = result[0].total if result and result[0].total else 0
	
	return {
		"value": round(flt(total), 2)
	}

@frappe.whitelist()
def get_revenue_this_month():
	"""Get revenue for current month"""
	first_day = get_first_day(today())
	last_day = get_last_day(today())
	
	result = frappe.db.sql("""
		SELECT SUM(total) as total
		FROM `tabLemonSqueezy Order`
		WHERE status = 'paid'
		AND order_date BETWEEN %s AND %s
	""", (first_day, last_day), as_dict=1)
	
	revenue = result[0].total if result and result[0].total else 0
	
	# Get previous month for comparison
	prev_month_first = get_first_day(add_months(today(), -1))
	prev_month_last = get_last_day(add_months(today(), -1))
	
	prev_result = frappe.db.sql("""
		SELECT SUM(total) as total
		FROM `tabLemonSqueezy Order`
		WHERE status = 'paid'
		AND order_date BETWEEN %s AND %s
	""", (prev_month_first, prev_month_last), as_dict=1)
	
	prev_revenue = prev_result[0].total if prev_result and prev_result[0].total else 0
	
	# Calculate percentage change
	if prev_revenue > 0:
		change_pct = ((flt(revenue) - flt(prev_revenue)) / flt(prev_revenue)) * 100
	else:
		change_pct = 100 if revenue > 0 else 0
	
	return {
		"value": round(flt(revenue), 2),
		"change_percent": round(change_pct, 1),
		"previous_value": round(flt(prev_revenue), 2)
	}

@frappe.whitelist()
def get_revenue_by_month(months=6):
	"""Get revenue trend by month"""
	months = int(months)
	revenue_data = []
	
	for i in range(months - 1, -1, -1):
		month_date = add_months(today(), -i)
		first_day = get_first_day(month_date)
		last_day = get_last_day(month_date)
		
		result = frappe.db.sql("""
			SELECT SUM(total) as total
			FROM `tabLemonSqueezy Order`
			WHERE status = 'paid'
			AND order_date BETWEEN %s AND %s
		""", (first_day, last_day), as_dict=1)
		
		revenue = result[0].total if result and result[0].total else 0
		
		revenue_data.append({
			"month": getdate(month_date).strftime("%b %Y"),
			"revenue": round(flt(revenue), 2)
		})
	
	return revenue_data

@frappe.whitelist()
def get_revenue_by_product(limit=5):
	"""Get revenue breakdown by product"""
	limit = int(limit)
	
	result = frappe.db.sql("""
		SELECT 
			product_name,
			SUM(total) as revenue,
			COUNT(*) as order_count
		FROM `tabLemonSqueezy Order`
		WHERE status = 'paid'
		AND product_name IS NOT NULL
		GROUP BY product_name
		ORDER BY revenue DESC
		LIMIT %s
	""", limit, as_dict=1)
	
	products = []
	for row in result:
		products.append({
			"name": row.product_name or "Unknown Product",
			"revenue": round(flt(row.revenue), 2),
			"order_count": row.order_count
		})
	
	return products

@frappe.whitelist()
def get_average_order_value():
	"""Calculate average order value"""
	result = frappe.db.sql("""
		SELECT AVG(total) as avg_value
		FROM `tabLemonSqueezy Order`
		WHERE status = 'paid'
	""", as_dict=1)
	
	avg_value = result[0].avg_value if result and result[0].avg_value else 0
	
	return {
		"value": round(flt(avg_value), 2)
	}
