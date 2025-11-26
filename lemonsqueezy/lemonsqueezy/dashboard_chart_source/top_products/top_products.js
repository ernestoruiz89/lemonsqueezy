frappe.provide('frappe.dashboards.chart_sources');

frappe.dashboards.chart_sources["Top Products"] = {
    method: "lemonsqueezy.lemonsqueezy.dashboard_metrics.get_top_products",
    filters: []
};
