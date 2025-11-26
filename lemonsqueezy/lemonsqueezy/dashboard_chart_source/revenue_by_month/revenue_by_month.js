frappe.provide('frappe.dashboards.chart_sources');

frappe.dashboards.chart_sources["Revenue by Month"] = {
    method: "lemonsqueezy.lemonsqueezy.dashboard_metrics.get_revenue_by_month",
    filters: []
};
