/* Clubhouse Autopilot Dashboard */

const params = new URLSearchParams(window.location.search);
const SITE_ID = params.get("site_id") || "";
const API = `/api/sites/${SITE_ID}`;

// --- Helpers ---

function $(sel) { return document.querySelector(sel); }

function fmtTime(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDate(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

function pct(n) {
  if (n == null) return "--";
  return Math.round(n * 100) + "%";
}

function confidenceBadge(label) {
  const cls = { high: "badge-high", medium: "badge-medium", low: "badge-low" }[label] || "badge-medium";
  return `<span class="badge ${cls}">${label || "unknown"}</span>`;
}

function trendArrow(trend) {
  const map = {
    improving: { arrow: "\u2191", cls: "trend-improving" },
    declining: { arrow: "\u2193", cls: "trend-declining" },
    stable:    { arrow: "\u2192", cls: "trend-stable" },
  };
  const t = map[trend] || map.stable;
  return `<span class="trend ${t.cls}">${t.arrow}</span>`;
}

function adoptionStatus(label) {
  if (label == null) return "";
  return `<span class="pill ${label ? "pill-adopted" : "pill-skipped"}">${label ? "Adopted" : "Skipped"}</span>`;
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// --- Clock ---

function updateClock() {
  const now = new Date();
  $("#clock-time").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  $("#clock-date").textContent = now.toLocaleDateString([], { weekday: "long", year: "numeric", month: "short", day: "numeric" });
}

// --- Panel Renderers ---

function renderForecast(data) {
  const el = $("#forecast-content");
  if (!data) { el.innerHTML = '<div class="no-data">No forecast available for today.</div>'; return; }

  const drinks = data.total_predicted_drinks ?? "--";
  const mode = data.staffing_mode ?? "--";
  const confLabel = data.confidence_label ?? "unknown";
  const rushCount = data.rush_count ?? 0;

  let html = `<div class="forecast-stats">
    <div class="stat"><span class="value">${drinks}</span><span class="label">Predicted Drinks</span></div>
    <div class="stat"><span class="value">${mode}</span><span class="label">Staffing Mode</span></div>
    <div class="stat"><span class="value">${rushCount}</span><span class="label">Rush Windows</span></div>
    <div class="stat">${confidenceBadge(confLabel)}<span class="label">Confidence</span></div>
  </div>`;

  if (data.weather) {
    const w = data.weather;
    html += `<div class="weather-row">
      <div class="item">${w.temp_c != null ? w.temp_c + "\u00b0C" : "--"}</div>
      <div class="item">${w.description || "--"}</div>
      <div class="item">Rain: ${w.rain_probability != null ? Math.round(w.rain_probability * 100) + "%" : "--"}</div>
      <div class="item">Humidity: ${w.humidity != null ? w.humidity + "%" : "--"}</div>
    </div>`;
  }

  if (data.event_multiplier && data.event_multiplier !== 1.0) {
    html += `<div class="event-banner">Event multiplier active: ${data.event_multiplier}x</div>`;
  }

  el.innerHTML = html;
}

function renderChart(hourly) {
  const el = $("#chart-content");
  if (!hourly || hourly.length === 0) { el.innerHTML = '<div class="no-data">No hourly data available.</div>'; return; }

  const max = Math.max(...hourly.map(h => h.predicted_workload || 0), 1);

  let html = '<div class="chart-container">';
  for (const h of hourly) {
    const val = h.predicted_workload || 0;
    const heightPct = (val / max) * 100;
    const rushCls = h.is_rush ? " rush" : "";
    html += `<div class="chart-bar-wrapper">
      <div class="chart-bar${rushCls}" style="height:${heightPct}%">
        <span class="bar-value">${Math.round(val)}</span>
      </div>
      <span class="bar-label">${h.hour_label || h.hour + ":00"}</span>
    </div>`;
  }
  html += "</div>";
  el.innerHTML = html;
}

function renderRush(rushWindows) {
  const el = $("#rush-content");
  if (!rushWindows || rushWindows.length === 0) { el.innerHTML = '<div class="no-data">No rush windows detected.</div>'; return; }

  let html = '<div class="rush-cards">';
  for (const rw of rushWindows) {
    const start = fmtTime(rw.start);
    const end = fmtTime(rw.end);
    const dur = rw.duration_minutes ?? "--";
    const drinks = rw.predicted_drinks ?? "--";

    html += `<div class="rush-card">
      <div class="rush-header">
        <span class="rush-time">${start} &ndash; ${end}</span>
        <span class="rush-meta">${dur} min &middot; ${drinks} drinks</span>
      </div>
      <div class="rush-actions">`;

    if (rw.switch_3p_time) {
      html += `<div class="action-item"><span class="action-icon">&#9654;</span> Switch 3P at ${fmtTime(rw.switch_3p_time)}</div>`;
    }
    if (rw.wally_start_time) {
      html += `<div class="action-item"><span class="action-icon">&#9881;</span> Start Wally at ${fmtTime(rw.wally_start_time)}</div>`;
    }
    if (rw.alert_time) {
      html += `<div class="action-item"><span class="action-icon">&#128276;</span> Alert at ${fmtTime(rw.alert_time)}</div>`;
    }

    html += `</div>`;

    if (rw.wally_volume_litres) {
      const split = rw.wally_split || {};
      const parts = Object.entries(split).map(([k, v]) => `${k}: ${v}L`).join(" / ");
      html += `<div class="wally-row">Wally: ${rw.wally_volume_litres}L total${parts ? " (" + parts + ")" : ""}</div>`;
    }

    html += `</div>`;
  }
  html += "</div>";
  el.innerHTML = html;
}

function renderAccuracy(accuracy, adoption) {
  const el = $("#accuracy-content");
  let html = '<div class="accuracy-strip">';

  if (accuracy) {
    const avg = accuracy.avg_accuracy != null ? Math.round(accuracy.avg_accuracy) + "%" : "--";
    const days = accuracy.days_measured ?? 7;
    html += `<div class="accuracy-metric">
      <div><span class="metric-value">${avg}</span>${trendArrow(accuracy.trend)}</div>
      <div class="metric-label">Rolling ${days}-day accuracy</div>
    </div>`;
  } else {
    html += `<div class="accuracy-metric"><div class="no-data">Accuracy data unavailable</div></div>`;
  }

  if (adoption) {
    const rate = adoption.adoption_rate != null ? Math.round(adoption.adoption_rate * 100) + "%" : "--";
    const meets = adoption.meets_target;
    const meetsCls = meets ? "yes" : "no";
    const meetsLabel = meets ? "On target" : "Below target";
    html += `<div class="accuracy-metric">
      <div class="metric-value">${rate}</div>
      <div class="metric-label">Adoption rate (${adoption.recommendations_adopted ?? 0}/${adoption.recommendations_total ?? 0})</div>
      <span class="target-met ${meetsCls}">${meetsLabel}</span>
    </div>`;
  } else {
    html += `<div class="accuracy-metric"><div class="no-data">Adoption data unavailable</div></div>`;
  }

  html += "</div>";

  if (accuracy && accuracy.alert) {
    html += `<div class="alert-banner">&#9888; ${accuracy.alert_reason || "Accuracy alert triggered"}</div>`;
  }

  el.innerHTML = html;
}

function renderRecs(records) {
  const el = $("#recs-content");
  if (!records || records.length === 0) { el.innerHTML = '<div class="no-data">No recent recommendations.</div>'; return; }

  let html = `<table class="rec-table">
    <thead><tr>
      <th>Time</th><th>Action</th><th>Owner</th><th>Status</th>
    </tr></thead><tbody>`;

  for (const r of records) {
    const time = fmtTime(r.action_timing);
    const type = (r.action_type || "").replace(/_/g, " ");
    const owner = r.owner_role || "--";
    const adopted = r.adopted;
    let statusHtml;
    if (adopted === true) statusHtml = '<span class="pill pill-adopted">Adopted</span>';
    else if (adopted === false) statusHtml = '<span class="pill pill-skipped">Skipped</span>';
    else statusHtml = '<span class="pill pill-pending">Pending</span>';

    html += `<tr>
      <td>${time}</td>
      <td>${type}</td>
      <td>${owner}</td>
      <td>${statusHtml}</td>
    </tr>`;
  }

  html += "</tbody></table>";
  el.innerHTML = html;
}

function renderROI(data) {
  const el = $("#roi-content");
  if (!data || !data.current_week) {
    el.innerHTML = '<div class="no-data">No weekly ROI data available.</div>';
    return;
  }

  const c = data.current_week || {};
  const d = data.deltas || {};

  const money = (cents) => (cents == null ? "--" : `$${Math.round(cents / 100).toLocaleString()}`);
  const pct = (n) => (n == null ? "--" : `${n.toFixed(1)}%`);
  const revHr = (cents) => (cents == null ? "--" : `$${Math.round(cents / 100).toLocaleString()}`);
  const deltaCls = (n) => (n == null || n === 0 ? "flat" : n > 0 ? "up" : "down");
  const deltaFmt = (n, suffix = "") => (n == null ? "N/A" : `${n > 0 ? "+" : ""}${n}${suffix}`);

  const html = `
    <div class="roi-headline">${data.headline || "Weekly ROI summary"}</div>
    <div class="roi-grid">
      <div class="roi-item">
        <div class="roi-label">Net Profit (Week)</div>
        <div class="roi-value">
          ${money(c.total_net_profit_cents)}
          <span class="roi-delta ${deltaCls(d.net_profit_cents_delta)}">${money(d.net_profit_cents_delta)}</span>
        </div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Revenue (Week)</div>
        <div class="roi-value">
          ${money(c.total_revenue_cents)}
          <span class="roi-delta ${deltaCls(d.revenue_cents_delta)}">${money(d.revenue_cents_delta)}</span>
        </div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Average Labor %</div>
        <div class="roi-value">
          ${pct(c.avg_labor_pct)}
          <span class="roi-delta ${deltaCls((d.labor_pct_delta_pp != null ? -d.labor_pct_delta_pp : null))}">
            ${deltaFmt(d.labor_pct_delta_pp, "pp")}
          </span>
        </div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Revenue per Labor Hour</div>
        <div class="roi-value">
          ${revHr(c.avg_revenue_per_labor_hour_cents)}
          <span class="roi-delta ${deltaCls(d.revenue_per_labor_hour_delta_pct)}">
            ${deltaFmt(d.revenue_per_labor_hour_delta_pct, "%")}
          </span>
        </div>
      </div>
    </div>
  `;

  el.innerHTML = html;
}

function renderStaffingVariance(data) {
  const el = $("#staffing-variance-content");
  if (!data || !Array.isArray(data.intervals) || data.intervals.length === 0) {
    el.innerHTML = '<div class="no-data">No staffing variance data available for today.</div>';
    return;
  }

  const summary = data.summary || {};
  const intervals = data.intervals || [];
  const displayRows = intervals.filter(i => i.status !== "no_workload").slice(0, 20);

  const statusPill = (status) => {
    const labelMap = {
      understaffed: "Understaffed",
      overstaffed: "Overstaffed",
      balanced: "Balanced",
      no_staff: "No Staff",
      no_workload: "No Workload",
    };
    return `<span class="status-pill status-${status}">${labelMap[status] || status}</span>`;
  };

  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip under">Understaffed: ${summary.understaffed_intervals || 0}</span>
      <span class="staffing-chip over">Overstaffed: ${summary.overstaffed_intervals || 0}</span>
      <span class="staffing-chip balance">Balanced: ${summary.balanced_intervals || 0}</span>
      <span class="staffing-chip">No Staff: ${summary.no_staff_intervals || 0}</span>
      <span class="staffing-chip">Peak WU: ${(summary.peak_workload_units || 0).toFixed(1)}</span>
    </div>
    <table class="staffing-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Workload</th>
          <th>Staff</th>
          <th>Target</th>
          <th>WU/Staff</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
  `;

  for (const row of displayRows) {
    html += `
      <tr>
        <td>${fmtTime(row.interval_start)}</td>
        <td>${(row.workload_units || 0).toFixed(1)}</td>
        <td>${row.staff_on ?? "--"}</td>
        <td>${row.expected_staff ?? "--"}</td>
        <td>${row.workload_per_staff != null ? row.workload_per_staff.toFixed(2) : "--"}</td>
        <td>${statusPill(row.status)}</td>
      </tr>
    `;
  }

  html += "</tbody></table>";
  el.innerHTML = html;
}

function fmtMoney(cents) {
  if (cents == null) return "--";
  return `$${Math.round(cents / 100).toLocaleString()}`;
}

function fmtSignedMoney(cents) {
  if (cents == null) return "--";
  const sign = cents > 0 ? "+" : "";
  return `${sign}$${Math.round(cents / 100).toLocaleString()}`;
}

function renderDailyEfficiency(data) {
  const el = $("#daily-efficiency-content");
  if (!data || !data.summary) {
    el.innerHTML = '<div class="no-data">No daily efficiency data available for today.</div>';
    return;
  }

  const s = data.summary || {};
  const peaks = data.peaks || {};
  const trade = peaks.trade || [];
  const staffing = peaks.staffing || [];
  const mismatch = peaks.mismatch || [];

  const row = (r) => `
    <tr>
      <td>${fmtTime(r.interval_start)}</td>
      <td>${r.orders_count ?? "--"}</td>
      <td>${fmtMoney(r.revenue_cents)}</td>
      <td>${(r.workload_units || 0).toFixed(1)}</td>
      <td>${r.staff_on ?? "--"}</td>
      <td>${r.workload_per_staff != null ? r.workload_per_staff.toFixed(2) : "--"}</td>
      <td>${r.status || "--"}</td>
    </tr>
  `;

  let html = `
    <div class="efficiency-summary">
      <span class="staffing-chip">Revenue: ${fmtMoney(s.total_revenue_cents)}</span>
      <span class="staffing-chip">Orders: ${s.total_orders ?? 0}</span>
      <span class="staffing-chip">Labor %: ${s.labor_pct != null ? s.labor_pct.toFixed(1) + "%" : "--"}</span>
      <span class="staffing-chip">Rev/Labor Hr: ${fmtMoney(s.revenue_per_labor_hour_cents)}</span>
      <span class="staffing-chip">Staff Hrs: ${s.deputy_staff_hours ?? 0}</span>
      <span class="staffing-chip">Labor Cost: ${fmtMoney(s.deputy_labor_cost_cents)}</span>
    </div>
  `;

  html += `<h3 class="efficiency-subhead">Top Trade Windows</h3>`;
  if (trade.length === 0) {
    html += '<div class="no-data">No trade windows detected.</div>';
  } else {
    html += `
      <table class="staffing-table">
        <thead>
          <tr>
            <th>Time</th><th>Orders</th><th>Revenue</th><th>Workload</th><th>Staff</th><th>WU/Staff</th><th>Status</th>
          </tr>
        </thead>
        <tbody>${trade.map(row).join("")}</tbody>
      </table>
    `;
  }

  html += `<h3 class="efficiency-subhead">Top Staffing Windows</h3>`;
  if (staffing.length === 0) {
    html += '<div class="no-data">No staffing windows detected.</div>';
  } else {
    html += `
      <table class="staffing-table">
        <thead>
          <tr>
            <th>Time</th><th>Orders</th><th>Revenue</th><th>Workload</th><th>Staff</th><th>WU/Staff</th><th>Status</th>
          </tr>
        </thead>
        <tbody>${staffing.map(row).join("")}</tbody>
      </table>
    `;
  }

  html += `<h3 class="efficiency-subhead">Mismatch Windows</h3>`;
  if (mismatch.length === 0) {
    html += '<div class="no-data">No mismatch windows detected.</div>';
  } else {
    html += `
      <table class="staffing-table">
        <thead>
          <tr>
            <th>Time</th><th>Orders</th><th>Revenue</th><th>Workload</th><th>Staff</th><th>WU/Staff</th><th>Status</th>
          </tr>
        </thead>
        <tbody>${mismatch.map(row).join("")}</tbody>
      </table>
    `;
  }

  el.innerHTML = html;
}

function renderNextActions(data) {
  const el = $("#next-actions-content");
  if (!data || !Array.isArray(data.actions) || data.actions.length === 0) {
    el.innerHTML = '<div class="no-data">No actionable recommendations yet for this date.</div>';
    return;
  }

  let html = '<div class="next-actions-list">';
  for (const action of data.actions) {
    const conf = action.confidence != null ? `${Math.round(action.confidence * 100)}%` : "--";
    const uplift = fmtMoney(action.expected_weekly_profit_uplift_cents);
    html += `
      <div class="next-action-card">
        <div class="next-action-head">
          <div class="next-action-title">${action.title || action.action_type || "Action"}</div>
          <span class="status-pill status-balanced">${conf} confidence</span>
        </div>
        <div class="next-action-reason">${action.reason || "--"}</div>
        <div class="next-action-metrics">
          <span class="staffing-chip">Type: ${action.action_type || "--"}</span>
          <span class="staffing-chip">Est. weekly impact: ${uplift}</span>
          ${action.window_start ? `<span class="staffing-chip">Window: ${fmtTime(action.window_start)}</span>` : ""}
          ${action.item ? `<span class="staffing-chip">Item: ${action.item}</span>` : ""}
        </div>
      </div>
    `;
  }
  html += "</div>";
  el.innerHTML = html;
}

function renderRosterPlan(data) {
  const el = $("#roster-plan-content");
  if (!data || !Array.isArray(data.days_plan) || data.days_plan.length === 0) {
    el.innerHTML = '<div class="no-data">No roster plan available yet.</div>';
    return;
  }

  const summary = data.summary || {};
  const rows = data.days_plan || [];
  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip">Days with predictions: ${summary.days_with_predictions ?? "--"}</span>
      <span class="staffing-chip">Days without predictions: ${summary.days_without_predictions ?? "--"}</span>
      <span class="staffing-chip">Window: ${data.start_date || "--"} (+${data.days || "--"}d)</span>
    </div>
    <table class="staffing-table">
      <thead>
        <tr><th>Date</th><th>Mode</th><th>Shifts</th><th>Hours</th><th>Labor Delta</th><th>Roles</th><th>Status</th></tr>
      </thead>
      <tbody>
  `;

  for (const row of rows) {
    const roles = Array.isArray(row.role_assignments)
      ? row.role_assignments.slice(0, 4).map((r) => `${r.role}:${r.zone}`).join(", ")
      : "--";
    const status = row.status || "unknown";
    html += `
      <tr>
        <td>${row.date || "--"}</td>
        <td>${row.workflow_mode || "--"}</td>
        <td>${row.recommended_shift_count ?? "--"}</td>
        <td>${row.recommended_total_hours != null ? row.recommended_total_hours : "--"}</td>
        <td>${fmtSignedMoney(row.estimated_labor_delta_cents)}</td>
        <td>${roles}</td>
        <td><span class="status-pill status-${status}">${status}</span></td>
      </tr>
    `;
  }

  html += "</tbody></table>";
  el.innerHTML = html;
}

function renderDataHealth(data) {
  const el = $("#data-health-content");
  if (!data || !Array.isArray(data.components)) {
    el.innerHTML = '<div class="no-data">Data health unavailable.</div>';
    return;
  }

  const badge = (status) => `<span class="status-pill status-${status}">${status}</span>`;
  const score = data.score != null ? `${Math.round(data.score * 100)}%` : "--";

  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip">Overall: ${badge(data.status || "unknown")}</span>
      <span class="staffing-chip">Health Score: ${score}</span>
      <span class="staffing-chip">As of: ${data.as_of ? fmtTime(data.as_of) : "--"}</span>
    </div>
    <table class="staffing-table">
      <thead>
        <tr><th>Source</th><th>Status</th><th>Latest</th><th>Age (days)</th><th>Notes</th></tr>
      </thead>
      <tbody>
  `;

  for (const c of data.components) {
    let notes = "";
    if (c.source === "square_orders") notes = `today_orders=${c.today_orders ?? 0}`;
    if (c.source === "deputy_rosters") notes = `next_14d_shifts=${c.next_14d_shifts ?? 0}`;
    if (c.source === "xero_cogs") notes = `connected=${c.connected ? "yes" : "no"}, xero_items=${c.xero_cost_items ?? 0}`;
    if (c.source === "data_quality_flags") notes = `active_flags=${Array.isArray(c.active_flags) ? c.active_flags.length : 0}`;
    html += `
      <tr>
        <td>${c.source}</td>
        <td>${badge(c.status || "unknown")}</td>
        <td>${c.latest_date || "--"}</td>
        <td>${c.age_days != null ? c.age_days : "--"}</td>
        <td>${notes || "--"}</td>
      </tr>
    `;
  }

  html += "</tbody></table>";
  el.innerHTML = html;
}

function renderDataQualityFlags(flagsPayload, diagnosticsPayload) {
  const el = $("#data-quality-flags-content");
  const flags = Array.isArray(flagsPayload?.flags) ? flagsPayload.flags : [];
  const diag = diagnosticsPayload || {};
  const suspected = !!diag.suspected_partial_ingest;
  const signals = Array.isArray(diag.signals) ? diag.signals : [];

  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip">Today diagnostics: <span class="status-pill status-${suspected ? "red" : "green"}">${suspected ? "suspected_partial" : "clear"}</span></span>
      <span class="staffing-chip">Today completed orders: ${diag?.day?.completed_orders ?? "--"}</span>
      <span class="staffing-chip">Today revenue: ${fmtMoney(diag?.day?.completed_revenue_cents)}</span>
      <span class="staffing-chip">Signals: ${signals.length}</span>
    </div>
  `;

  if (signals.length > 0) {
    html += `<div class="quality-flags-signals">${signals.map((s) => `<span class="status-pill status-yellow">${s}</span>`).join(" ")}</div>`;
  }

  if (flags.length === 0) {
    html += '<div class="no-data">No active data quality flags.</div>';
    el.innerHTML = html;
    return;
  }

  if (flags.some((f) => f.flag_type === "partial_ingest")) {
    html += '<div class="no-data">If you have verified this day was complete, clear the flag via API: DELETE /analysis/data-quality/flags/partial-ingest?flag_date=YYYY-MM-DD</div>';
  }

  html += `
    <table class="staffing-table">
      <thead><tr><th>Date</th><th>Type</th><th>Severity</th><th>Source</th><th>Reason</th></tr></thead>
      <tbody>
        ${flags.map((f) => `
          <tr>
            <td>${f.flag_date || "--"}</td>
            <td>${f.flag_type || "--"}</td>
            <td><span class="status-pill status-${f.severity === "high" ? "red" : "yellow"}">${f.severity || "--"}</span></td>
            <td>${f.source || "--"}</td>
            <td>${f.reason || "--"}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  el.innerHTML = html;
}

function renderPipelineHealth(data, runsPayload) {
  const el = $("#pipeline-health-content");
  if (!data) {
    el.innerHTML = '<div class="no-data">Pipeline health unavailable.</div>';
    return;
  }

  const badge = (status) => `<span class="status-pill status-${status}">${status}</span>`;
  const pct = (v) => (v == null ? "--" : `${Math.round(v * 100)}%`);
  const runs = Array.isArray(runsPayload?.runs) ? runsPayload.runs.slice(0, 10) : [];

  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip">Overall: ${badge(data.status || "unknown")}</span>
      <span class="staffing-chip">Success: ${pct(data.overall_success_rate)}</span>
      <span class="staffing-chip">Runs: ${data.total_runs ?? 0}</span>
      <span class="staffing-chip">Errors: ${data.error_runs ?? 0}</span>
      <span class="staffing-chip">Skipped: ${data.skipped_runs ?? 0}</span>
    </div>
  `;

  if (Array.isArray(data.components) && data.components.length > 0) {
    html += `
      <table class="staffing-table">
        <thead><tr><th>Job</th><th>Success</th><th>Runs</th><th>Errors</th><th>Last Run</th></tr></thead>
        <tbody>
          ${data.components.map((c) => `
            <tr>
              <td>${c.job_name}</td>
              <td>${pct(c.success_rate)}</td>
              <td>${c.total_runs}</td>
              <td>${c.error_runs}</td>
              <td>${c.last_started_at ? fmtTime(c.last_started_at) : "--"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  html += `<h3 class="efficiency-subhead">Recent Runs</h3>`;
  if (runs.length === 0) {
    html += '<div class="no-data">No recent runs logged yet.</div>';
  } else {
    html += `
      <table class="staffing-table">
        <thead><tr><th>Time</th><th>Job</th><th>Status</th><th>Duration</th><th>Error</th></tr></thead>
        <tbody>
          ${runs.map((r) => `
            <tr>
              <td>${r.started_at ? fmtTime(r.started_at) : "--"}</td>
              <td>${r.job_name || "--"}</td>
              <td>${badge(r.status || "unknown")}</td>
              <td>${r.duration_ms != null ? `${r.duration_ms}ms` : "--"}</td>
              <td>${r.error_text || "--"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  el.innerHTML = html;
}

// --- Main Load ---

async function loadDashboard() {
  const xeroLink = $("#xero-setup-link");
  if (xeroLink) {
    xeroLink.href = SITE_ID ? `/xero/setup?site_id=${encodeURIComponent(SITE_ID)}` : "/xero/setup";
  }

  if (!SITE_ID) {
    $(".dashboard").innerHTML = `<div class="panel"><h2>No Site Selected</h2>
      <p>Add <code>?site_id=YOUR_SITE_ID</code> to the URL to load a dashboard.</p></div>`;
    return;
  }

  updateClock();
  setInterval(updateClock, 1000);

  // Fetch site status
  fetchJSON(`${API}/status`)
    .then(site => {
      $("#site-name").textContent = site.name || "Clubhouse Autopilot";
      $("#site-sub").textContent = `Site ID: ${site.site_id || SITE_ID}`;
    })
    .catch(() => {
      $("#site-sub").textContent = `Site ID: ${SITE_ID}`;
    });

  // Fetch today's prediction
  // The /predictions/today endpoint returns DB shape with forecast_data wrapper;
  // normalize to a flat shape the renderers expect.
  fetchJSON(`${API}/predictions/today`)
    .then(raw => {
      const fd = raw.forecast_data || {};
      const data = {
        total_predicted_drinks: fd.total_predicted_drinks ?? raw.total_predicted_drinks,
        total_predicted_workload: fd.total_predicted_workload ?? raw.total_predicted_workload,
        staffing_mode: fd.staffing_mode ?? raw.staffing_mode,
        confidence_label: fd.confidence_label ?? raw.confidence_label,
        rush_count: fd.rush_count ?? raw.rush_count ?? 0,
        event_multiplier: fd.forecast?.event_multiplier ?? raw.event_multiplier ?? 1.0,
        weather: fd.weather ?? raw.weather,
        forecast: fd.forecast ?? raw.forecast,
        rush_windows: fd.rush_windows ?? raw.rush_windows,
      };
      renderForecast(data);
      const hourly = data.forecast ? data.forecast.hourly : null;
      renderChart(hourly);
      const rushWindows = data.rush_windows || (data.forecast ? data.forecast.rush_windows : null);
      renderRush(rushWindows);
    })
    .catch(() => {
      renderForecast(null);
      renderChart(null);
      renderRush(null);
    });

  // Fetch accuracy
  fetchJSON(`${API}/analysis/accuracy?days_back=7`)
    .then(acc => {
      // Fetch adoption with last 7 days range
      const end = new Date().toISOString().slice(0, 10);
      const start = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
      return fetchJSON(`${API}/analysis/adoption?start_date=${start}&end_date=${end}`)
        .then(adp => renderAccuracy(acc, adp))
        .catch(() => renderAccuracy(acc, null));
    })
    .catch(() => renderAccuracy(null, null));

  // Fetch weekly ROI summary
  fetchJSON(`${API}/analysis/weekly-roi`)
    .then(renderROI)
    .catch(() => renderROI(null));

  // Fetch staffing variance windows for today
  fetchJSON(`${API}/analysis/staffing-variance`)
    .then(renderStaffingVariance)
    .catch(() => renderStaffingVariance(null));

  // Fetch daily efficiency summary (trade vs staffing)
  fetchJSON(`${API}/analysis/daily-efficiency`)
    .then(renderDailyEfficiency)
    .catch(() => renderDailyEfficiency(null));

  // Fetch next best actions
  fetchJSON(`${API}/analysis/recommendations/next-actions`)
    .then(renderNextActions)
    .catch(() => renderNextActions(null));

  // Fetch 14-day workflow roster plan
  fetchJSON(`${API}/analysis/workflow/roster-plan?days=14`)
    .then(renderRosterPlan)
    .catch(() => renderRosterPlan(null));

  // Fetch data health
  fetchJSON(`${API}/analysis/data-health`)
    .then(renderDataHealth)
    .catch(() => renderDataHealth(null));

  // Fetch active data-quality flags + today diagnostics
  Promise.all([
    fetchJSON(`${API}/analysis/data-quality/flags?active_only=true&limit=100`),
    fetchJSON(`${API}/analysis/data-quality/day-diagnostics`),
  ])
    .then(([flags, diag]) => renderDataQualityFlags(flags, diag))
    .catch(() => renderDataQualityFlags(null, null));

  // Fetch pipeline health + recent runs
  Promise.all([
    fetchJSON(`${API}/analysis/pipeline-health?hours=24`),
    fetchJSON(`${API}/analysis/pipeline-runs?limit=20`),
  ])
    .then(([health, runs]) => renderPipelineHealth(health, runs))
    .catch(() => renderPipelineHealth(null, null));

  // Fetch delivery log
  fetchJSON(`${API}/delivery/log?limit=10`)
    .then(data => {
      const records = Array.isArray(data) ? data : (data.records || data.log || []);
      renderRecs(records);
    })
    .catch(() => renderRecs(null));
}

loadDashboard();
