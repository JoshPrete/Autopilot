const state = {
  parsedItems: [],
  orderId: null,
};

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || data.error || res.statusText);
  }
  return data;
}

function renderItems(items) {
  const root = document.getElementById("items");
  root.innerHTML = "";

  if (!items.length) {
    root.innerHTML = "<p class=\"muted\">No items parsed yet.</p>";
    return;
  }

  items.forEach((item, idx) => {
    const div = document.createElement("div");
    div.className = "item";
    const mods = item.modifiers || [];
    div.innerHTML = `
      <div class="item-header">
        <strong>${item.name}</strong>
        <span class="pill">qty ${item.quantity}</span>
        <span class="pill">confidence ${(item.confidence * 100).toFixed(0)}%</span>
      </div>
      <div class="meta">catalog: ${item.variation_id || item.catalog_object_id || "n/a"}</div>
      <div class="mods">${mods.map((m) => `<span>${m.name}</span>`).join("") || ""}</div>
      <div class="meta">notes: ${item.notes || "-"}</div>
    `;
    root.appendChild(div);
  });
}

async function refreshEnv() {
  try {
    const data = await fetchJson("/health");
    document.getElementById("env").textContent = `env: ${data.square_env} | catalog: ${data.catalog_items}`;
  } catch (err) {
    document.getElementById("env").textContent = `env: unknown`;
  }
}

async function loadWebhooks() {
  try {
    const data = await fetchJson("/webhooks/recent");
    const root = document.getElementById("webhooks");
    root.innerHTML = "";
    if (!data.events.length) {
      root.innerHTML = "<p class=\"muted\">No webhook events yet.</p>";
      return;
    }
    data.events.slice().reverse().forEach((event) => {
      const div = document.createElement("div");
      div.className = "item";
      div.innerHTML = `<div><strong>${event.type}</strong></div><div class=\"meta\">${event.event_id || ""} ${event.created_at || ""}</div>`;
      root.appendChild(div);
    });
  } catch (err) {
    // ignore
  }
}

async function parseTranscript() {
  const transcript = document.getElementById("transcript").value.trim();
  if (!transcript) return;

  const data = await fetchJson("/voice/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcript }),
  });

  state.parsedItems = data.items || [];
  renderItems(state.parsedItems);

  const warnings = document.getElementById("warnings");
  warnings.innerHTML = data.warnings?.length ? data.warnings.join(" ") : "";
}

async function confirmOrder() {
  if (!state.parsedItems.length) {
    throw new Error("No parsed items to confirm");
  }

  const lineItems = state.parsedItems.map((item) => {
    const entry = {
      quantity: String(item.quantity || 1),
    };

    if (item.variation_id) {
      entry.catalog_object_id = item.variation_id;
    } else if (item.catalog_object_id) {
      entry.catalog_object_id = item.catalog_object_id;
    } else {
      entry.name = item.name;
    }

    if (item.modifiers && item.modifiers.length) {
      entry.modifiers = item.modifiers
        .filter((mod) => mod.catalog_object_id)
        .map((mod) => ({ catalog_object_id: mod.catalog_object_id }));
    }

    return entry;
  });

  const payload = {
    line_items: lineItems,
    pickup_at: new Date(Date.now() + 10 * 60000).toISOString(),
    recipient_name: "Walk-in",
  };

  const data = await fetchJson("/voice/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  state.orderId = data.order_id;
  document.getElementById("orderStatus").textContent = `Order ${data.order_id} (${data.state})`;
}

async function checkoutOrder() {
  if (!state.orderId) {
    throw new Error("Create an order first");
  }

  const deviceId = document.getElementById("deviceId").value.trim();
  const amount = Number(document.getElementById("amount").value);
  const currency = document.getElementById("currency").value.trim().toUpperCase();

  const payload = {
    order_id: state.orderId,
    note: "Voice POC",
  };

  if (deviceId) payload.device_id = deviceId;
  if (!Number.isNaN(amount)) {
    payload.amount_money = { amount, currency };
  }

  const data = await fetchJson("/voice/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  document.getElementById("checkoutStatus").textContent = `Checkout ${data.checkout_id} (${data.status})`;
}

async function sandboxPay() {
  if (!state.orderId) {
    throw new Error("Create an order first");
  }
  const amount = Number(document.getElementById("amount").value);
  const currency = document.getElementById("currency").value.trim().toUpperCase();
  const sourceId = document.getElementById("sourceId").value.trim();

  const payload = {
    order_id: state.orderId,
    amount_money: { amount, currency },
    source_id: sourceId || undefined,
    note: "Sandbox payment",
  };

  const data = await fetchJson("/voice/pay-sandbox", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  document.getElementById("checkoutStatus").textContent = `Sandbox payment ${data.payment_id} (${data.status})`;
}

async function mockPay() {
  if (!state.orderId) {
    throw new Error("Create an order first");
  }
  const data = await fetchJson("/voice/mock-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order_id: state.orderId, status: "COMPLETED" }),
  });

  document.getElementById("checkoutStatus").textContent = `Mock payment ${data.order_id} (${data.status})`;
  loadWebhooks();
}

function bind() {
  document.getElementById("parseBtn").addEventListener("click", () => {
    parseTranscript().catch((err) => alert(err.message));
  });
  document.getElementById("clearBtn").addEventListener("click", () => {
    document.getElementById("transcript").value = "";
  });
  document.getElementById("confirmBtn").addEventListener("click", () => {
    confirmOrder().catch((err) => alert(err.message));
  });
  document.getElementById("checkoutBtn").addEventListener("click", () => {
    checkoutOrder().catch((err) => alert(err.message));
  });
  document.getElementById("sandboxPayBtn").addEventListener("click", () => {
    sandboxPay().catch((err) => alert(err.message));
  });
  document.getElementById("mockPayBtn").addEventListener("click", () => {
    mockPay().catch((err) => alert(err.message));
  });

  setInterval(loadWebhooks, 5000);
}

refreshEnv();
loadWebhooks();
renderItems([]);
bind();
