frappe.provide('frappe.dashboards.chart_sources');

frappe.dashboards.chart_sources["Revenue by Product"] = {
    method: "lemonsqueezy.lemonsqueezy.dashboard_metrics.get_revenue_by_product",
    filters: []
};
