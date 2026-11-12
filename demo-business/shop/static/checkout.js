// Kitebase checkout with PayPal Buttons. A timed-out capture leaves `retryOrder` set, so the
// next payment is for the same order: the shop's missing "already paid" check is the bug the
// double-charge scenario shows.
(function () {
  const form = document.getElementById("checkout");
  const status = document.getElementById("checkout-status");
  let retryOrder = null;

  function say(text, kind) {
    status.textContent = text;
    status.className = kind || "";
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    return { status: response.status, data };
  }

  function start() {
    if (!window.paypal || !form) return;
    window.paypal
      .Buttons({
        onClick: (_, actions) => {
          if (!retryOrder && !form.email.value) {
            say("Enter an email address first.", "error");
            return actions.reject();
          }
          return actions.resolve();
        },
        createOrder: async () => {
          const body = retryOrder
            ? { order_ref: retryOrder }
            : { plan_id: form.plan_id.value, email: form.email.value, name: form.name.value };
          const { status: code, data } = await post("/api/checkout", body);
          if (code !== 200) {
            say(data.message || "Checkout failed.", "error");
            throw new Error(data.error || "checkout");
          }
          form.dataset.orderRef = data.order_ref;
          return data.paypal_order_id;
        },
        onApprove: async (data) => {
          say("Confirming your payment...");
          const result = await post("/api/checkout/capture", {
            order_ref: form.dataset.orderRef,
            paypal_order_id: data.orderID,
          });
          if (result.status === 200) {
            window.location = "/orders/" + encodeURIComponent(result.data.order_ref);
          } else if (result.status === 504) {
            retryOrder = result.data.order_ref;
            say(result.data.message + " (order " + retryOrder + ")", "error");
          } else {
            say(result.data.message || "Payment failed.", "error");
            if (result.data.order_ref) {
              const link = document.createElement("a");
              link.href = "/orders/" + encodeURIComponent(result.data.order_ref);
              link.textContent = " View order";
              status.appendChild(link);
            }
          }
        },
        onError: () => say("PayPal could not start the payment.", "error"),
      })
      .render("#paypal-buttons");
  }

  if (document.readyState === "complete") start();
  else window.addEventListener("load", start);
})();
