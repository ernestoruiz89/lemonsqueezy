frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Subscriptions Trend"] = {
    method: "lemonsqueezy.lemonsqueezy.dashboard_metrics.get_subscriptions_trend",
    filters: [
        {
            fieldname: "months",
            label: __("Months"),
            fieldtype: "Int",
            default: 6
        }
    ]
};
