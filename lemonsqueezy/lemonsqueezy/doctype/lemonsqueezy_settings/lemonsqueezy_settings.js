// Copyright (c) 2023, User and contributors
// For license information, please see license.txt

frappe.ui.form.on("LemonSqueezy Settings", {
    refresh: function (frm) {
        // Add Test Connection button
        if (!frm.is_new()) {
            frm.add_custom_button(__("Test Connection"), function () {
                frappe.call({
                    method: "lemonsqueezy.lemonsqueezy.doctype.lemonsqueezy_settings.lemonsqueezy_settings.test_connection",
                    args: {
                        name: frm.doc.name,
                    },
                    freeze: true,
                    freeze_message: __("Testing connection..."),
                    callback: function (r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert(
                                {
                                    message: __("Connection successful!"),
                                    indicator: "green",
                                },
                                5
                            );
                        }
                    },
                });
            });

            // Add Copy Webhook URL button
            frm.add_custom_button(__("Copy Webhook URL"), function () {
                const webhook_url = `${window.location.origin}/api/method/lemonsqueezy.lemonsqueezy.api.handle_webhook`;
                navigator.clipboard.writeText(webhook_url).then(
                    function () {
                        frappe.show_alert(
                            {
                                message: __("Webhook URL copied to clipboard"),
                                indicator: "green",
                            },
                            3
                        );
                    },
                    function () {
                        frappe.msgprint({
                            title: __("Webhook URL"),
                            message: webhook_url,
                            indicator: "blue",
                        });
                    }
                );
            });
        }
    },
});
