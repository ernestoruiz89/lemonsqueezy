frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Subscription Distribution"] = {
    method: "lemonsqueezy.lemonsqueezy.dashboard_metrics.get_subscription_distribution"
};
