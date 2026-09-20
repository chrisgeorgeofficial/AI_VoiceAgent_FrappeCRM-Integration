// Frappe Client Script - DocType: CRM Lead, Apply to: Form
//
// Adds a "Call with AI Agent" button that asks the voice agent to ring the
// lead. Create it at:  /app/client-script/new
//
// Note the port: the voice agent runs on 5000, while the CRM desk you are
// reading this in is on 8000. Pointing this at 8000 would just call Frappe.
frappe.ui.form.on("CRM Lead", {
    refresh(frm) {
        frm.add_custom_button("Call with AI Agent", async () => {
            const number = frm.doc.mobile_no || frm.doc.phone;
            if (!number) {
                frappe.msgprint("This lead has no phone number.");
                return;
            }

            frappe.show_alert({ message: `Calling ${number}...`, indicator: "blue" });

            try {
                const response = await fetch(
                    "http://127.0.0.1:5000/voice/trigger-outbound",
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            phone_number: number,
                            lead_id: frm.doc.name,
                        }),
                    }
                );

                const body = await response.json();
                if (!response.ok) {
                    // Twilio's own message is the useful part - on a trial
                    // account an unverified number is the usual reason.
                    frappe.msgprint({
                        title: "Could not place the call",
                        message: body.error || JSON.stringify(body),
                        indicator: "red",
                    });
                    return;
                }

                frappe.show_alert({
                    message: `Ringing - call ${body.call_sid}`,
                    indicator: "green",
                });
            } catch (err) {
                // A failed fetch here is almost always the agent not running,
                // or CORS_ORIGINS not listing this site.
                frappe.msgprint({
                    title: "Could not reach the voice agent",
                    message: `${err}<br><br>Is it running on port 5000?`,
                    indicator: "red",
                });
            }
        });
    },
});
