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

function loadAccuracyDashboard(daysBack) {
  const el = $("#accuracy-content");
  el.className = "loading";
  el.textContent = "Loading metrics...";
  fetchJSON(`${API}/analysis/accuracy-dashboard?days_back=${daysBack}`)
    .then(data => renderAccuracyDashboard(data))
    .catch(() => {
      el.className = "";
      el.innerHTML = '<div class="no-data">Accuracy dashboard unavailable.</div>';
    });
}

function renderAccuracyDashboard(data) {
  const el = $("#accuracy-content");
  el.className = "";
  if (!data || data.days_measured === 0) {
    el.innerHTML = '<div class="no-data">No accuracy data available for this period.</div>';
    return;
  }

  const s = data.summary || {};
  let html = "";

  // --- Summary strip ---
  const avg = s.avg_accuracy != null ? Math.round(s.avg_accuracy) + "%" : "--";
  const absErr = s.avg_abs_error != null ? s.avg_abs_error + " drinks" : "--";
  const bias = s.avg_signed_error != null ? (s.avg_signed_error > 0 ? "+" : "") + s.avg_signed_error + " drinks" : "--";
  html += `<div class="accuracy-strip">
    <div class="accuracy-metric">
      <div><span class="metric-value">${avg}</span>${trendArrow(s.trend)}</div>
      <div class="metric-label">${data.days_measured}-day avg accuracy</div>
    </div>
    <div class="accuracy-metric">
      <div class="metric-value">${absErr}</div>
      <div class="metric-label">Avg absolute error</div>
    </div>
    <div class="accuracy-metric">
      <div class="metric-value">${bias}</div>
      <div class="metric-label">Avg bias (${(s.avg_signed_error != null && s.avg_signed_error > 0) ? "over" : "under"}-predicting)</div>
    </div>
    <div class="accuracy-metric">
      <div class="metric-value">${data.days_measured}</div>
      <div class="metric-label">Days measured</div>
    </div>
  </div>`;

  if (data.alert) {
    html += `<div class="alert-banner">&#9888; ${data.alert_reason || "Accuracy alert triggered"}</div>`;
  }

  // --- Trend chart ---
  if (data.daily_trend && data.daily_trend.length > 0) {
    html += `<h3 class="efficiency-subhead">Daily Accuracy Trend</h3>`;
    let trendData = data.daily_trend;
    // Group into weekly averages for 60+ day ranges
    if (data.days_back >= 60) {
      const weeks = [];
      for (let i = 0; i < trendData.length; i += 7) {
        const chunk = trendData.slice(i, i + 7);
        const avgAcc = chunk.reduce((s, d) => s + d.accuracy, 0) / chunk.length;
        const avgRoll = chunk.reduce((s, d) => s + d.rolling_7d_avg, 0) / chunk.length;
        weeks.push({ date: chunk[0].date, accuracy: Math.round(avgAcc * 10) / 10, rolling_7d_avg: Math.round(avgRoll * 10) / 10 });
      }
      trendData = weeks;
    }
    const maxAcc = 100;
    const targetPct = 85;
    const targetBottom = ((maxAcc - targetPct) / maxAcc) * 100;
    html += `<div class="accuracy-trend-chart">`;
    html += `<div class="accuracy-target-line" style="bottom:${100 - targetBottom}%"></div>`;
    for (const d of trendData) {
      const heightPct = (d.accuracy / maxAcc) * 100;
      let barCls = "accuracy-bar-green";
      if (d.accuracy < 75) barCls = "accuracy-bar-red";
      else if (d.accuracy < 85) barCls = "accuracy-bar-yellow";
      html += `<div class="chart-bar-wrapper">
        <div class="chart-bar ${barCls}" style="height:${heightPct}%" title="${d.date}: ${d.accuracy}%">
          <span class="bar-value">${Math.round(d.accuracy)}</span>
        </div>
        <span class="bar-label">${d.date.slice(5)}</span>
      </div>`;
    }
    html += `</div>`;
  }

  // --- Predicted vs Actual table (last 10) ---
  if (data.predicted_vs_actual && data.predicted_vs_actual.length > 0) {
    html += `<h3 class="efficiency-subhead">Predicted vs Actual (Last 10)</h3>`;
    const rows = data.predicted_vs_actual.slice(-10);
    html += `<table class="staffing-table">
      <thead><tr><th>Date</th><th>Predicted</th><th>Actual</th><th>Error</th><th>Accuracy</th></tr></thead>
      <tbody>`;
    for (const r of rows) {
      const accPct = (100 - r.error_pct).toFixed(1);
      html += `<tr>
        <td>${r.date}</td>
        <td>${r.predicted}</td>
        <td>${r.actual}</td>
        <td>${r.error} (${r.error_pct}%)</td>
        <td>${accPct}%</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  // --- DOW bars ---
  if (data.by_dow && data.by_dow.length > 0) {
    html += `<h3 class="efficiency-subhead">Accuracy by Day of Week</h3>`;
    const best = Math.max(...data.by_dow.map(d => d.avg_accuracy));
    const worst = Math.min(...data.by_dow.map(d => d.avg_accuracy));
    for (const d of data.by_dow) {
      const widthPct = (d.avg_accuracy / 100) * 100;
      let barCls = "";
      if (data.by_dow.length > 1 && d.avg_accuracy === best) barCls = " best";
      else if (data.by_dow.length > 1 && d.avg_accuracy === worst) barCls = " worst";
      const biasStr = d.avg_signed_error != null ? ((d.avg_signed_error > 0 ? "+" : "") + d.avg_signed_error) : "--";
      html += `<div class="dow-row">
        <span class="dow-label">${d.day_name}</span>
        <div class="dow-bar-track">
          <div class="dow-bar${barCls}" style="width:${widthPct}%">${d.avg_accuracy}%</div>
        </div>
        <span class="dow-bias">${biasStr}</span>
      </div>`;
    }
  }

  // --- Calibration chips (confidence bands + staffing modes) ---
  if ((data.by_confidence && data.by_confidence.length > 0) || (data.by_staffing_mode && data.by_staffing_mode.length > 0)) {
    html += `<h3 class="efficiency-subhead">Calibration &amp; Staffing Modes</h3>`;
    html += `<div class="efficiency-summary">`;
    if (data.by_confidence) {
      for (const b of data.by_confidence) {
        html += `<span class="staffing-chip">${b.band} conf (${b.label}): ${b.avg_accuracy}% (n=${b.sample_count})</span>`;
      }
    }
    if (data.by_staffing_mode) {
      for (const m of data.by_staffing_mode) {
        html += `<span class="staffing-chip">${m.mode}: ${m.avg_accuracy}% (n=${m.sample_count})</span>`;
      }
    }
    if (s.calibrated === false) {
      html += `<span class="staffing-chip under">&#9888; Miscalibrated: high-confidence predictions are not more accurate than low</span>`;
    } else if (s.calibrated === true) {
      html += `<span class="staffing-chip balance">&#10003; Confidence scores well-calibrated</span>`;
    }
    html += `</div>`;
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

function renderBottomLineScorecard(data) {
  const el = $("#bottom-line-scorecard-content");
  if (!data || !data.kpis || !data.window) {
    el.innerHTML = '<div class="no-data">No bottom-line scorecard data available.</div>';
    return;
  }

  const k = data.kpis || {};
  const t = data.trend || {};
  const d = t.deltas || {};
  const dirs = t.directions || {};
  const actions = data.actions || {};
  const ft = data.financial_truth || {};
  const targets = data.targets || {};
  const targetCurrent = targets.current || {};
  const targetGaps = targets.gaps || {};
  const primaryLever = targets.primary_lever || {};

  const fmtPct = (n) => (n == null ? "--" : `${n.toFixed(1)}%`);
  const fmtSigned = (n, suffix = "", decimals = 1) => {
    if (n == null) return "N/A";
    const value = Number(n);
    if (Number.isNaN(value)) return "N/A";
    return `${value > 0 ? "+" : ""}${value.toFixed(decimals)}${suffix}`;
  };
  const score = (n) => (n == null ? "--" : `${Math.round(n * 100)}%`);
  const tone = (direction) => {
    if (direction === "improving") return "up";
    if (direction === "declining") return "down";
    return "flat";
  };
  const typeLabel = (s) => (s || "--").replace(/_/g, " ");

  let html = `
    <div class="roi-headline">${data.headline || "Bottom-line scorecard"}</div>
    <div class="scorecard-meta">
      <span class="staffing-chip">Window: ${data.window.start_date || "--"} to ${data.window.end_date || "--"} (${data.window.days || "--"}d)</span>
      <span class="staffing-chip">Compare: ${data.window.compare_days || "--"}d vs prior</span>
      <span class="staffing-chip">Financial truth: ${ft.mode || "estimated_fallback"}</span>
      <span class="staffing-chip">Truth coverage: ${(ft.coverage_days ?? 0)}/${(ft.window_days ?? data.window.days ?? "--")} days</span>
      <span class="staffing-chip">Insights: ${(data.intelligence || {}).insights_generated ?? 0}</span>
      <span class="staffing-chip">High-priority insights: ${(data.intelligence || {}).high_priority_insights ?? 0}</span>
    </div>
    <div class="roi-grid">
      <div class="roi-item">
        <div class="roi-label">Net Profit (Window)</div>
        <div class="roi-value">
          ${fmtMoney(k.total_net_profit_cents)}
          <span class="roi-delta ${tone(dirs.net_profit)}">${fmtSignedMoney(d.net_profit_cents)}</span>
        </div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Revenue (Window)</div>
        <div class="roi-value">${fmtMoney(k.total_revenue_cents)}</div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Actual Incoming (Xero)</div>
        <div class="roi-value">${fmtMoney(ft.income_cents)}</div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Actual Outgoing (Xero)</div>
        <div class="roi-value">${fmtMoney(ft.expense_cents)}</div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Overhead Proxy</div>
        <div class="roi-value">${fmtMoney(ft.overhead_proxy_cents)}</div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Actual Net Cash (Xero)</div>
        <div class="roi-value">${fmtMoney(ft.net_cash_cents)}</div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Net Margin</div>
        <div class="roi-value">${fmtPct(k.net_margin_pct)}</div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Average Labor %</div>
        <div class="roi-value">
          ${fmtPct(k.avg_labor_pct)}
          <span class="roi-delta ${tone(dirs.labor_pct)}">${fmtSigned(d.labor_pct_delta_pp, "pp")}</span>
        </div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Revenue per Labor Hour</div>
        <div class="roi-value">
          ${fmtMoney(k.avg_revenue_per_labor_hour_cents)}
          <span class="roi-delta ${tone(dirs.revenue_per_labor_hour)}">${fmtSigned(d.revenue_per_labor_hour_delta_pct, "%")}</span>
        </div>
      </div>
      <div class="roi-item">
        <div class="roi-label">Efficiency Score (Current vs Prior)</div>
        <div class="roi-value">
          ${score((t.current_window || {}).efficiency_score)}
          <span class="roi-delta ${tone(dirs.efficiency_score)}">${fmtSigned(d.efficiency_score_delta_pp, "pp")}</span>
        </div>
      </div>
    </div>
  `;

  html += `
    <h3 class="efficiency-subhead">Realized Impact Attribution</h3>
    <div class="efficiency-summary">
      <span class="staffing-chip">Recommendations: ${actions.recommendations_generated ?? 0}</span>
      <span class="staffing-chip">Adopted: ${actions.recommendations_adopted ?? 0}</span>
      <span class="staffing-chip">Adoption rate: ${actions.adoption_rate != null ? `${Math.round(actions.adoption_rate * 100)}%` : "--"}</span>
      <span class="staffing-chip">Realized actions: ${actions.realized_actions ?? 0}</span>
      <span class="staffing-chip">Avg realized weekly profit: ${fmtMoney(actions.avg_realized_weekly_profit_delta_cents)}</span>
      <span class="staffing-chip">Total realized weekly profit: ${fmtMoney(actions.total_realized_weekly_profit_delta_cents)}</span>
    </div>
  `;

  if (targetCurrent.revenue_cents != null) {
    html += `
      <h3 class="efficiency-subhead">Margin Target Gap</h3>
      <div class="efficiency-summary">
        <span class="staffing-chip">Primary lever: ${primaryLever.focus || "--"}</span>
        <span class="staffing-chip">Labor: ${fmtPct(targetCurrent.labor_pct)} / target <= ${targets.targets?.labor_pct_high != null ? `${targets.targets.labor_pct_high.toFixed(0)}%` : "--"}</span>
        <span class="staffing-chip">COGS: ${fmtPct(targetCurrent.cogs_pct)} / target <= ${targets.targets?.cogs_pct_high != null ? `${targets.targets.cogs_pct_high.toFixed(0)}%` : "--"}</span>
        <span class="staffing-chip">Prime cost: ${fmtPct(targetCurrent.prime_cost_pct)} / target <= ${targets.targets?.prime_cost_pct_high != null ? `${targets.targets.prime_cost_pct_high.toFixed(0)}%` : "--"}</span>
        <span class="staffing-chip">Margin basis: ${fmtPct(targetCurrent.margin_basis_net_margin_pct)} (${targetCurrent.margin_basis_source || "operational_proxy"})</span>
      </div>
      <div class="efficiency-summary">
        <span class="staffing-chip">Labor reduction needed: ${fmtMoney(targetGaps.weekly_labor_reduction_needed_cents)}</span>
        <span class="staffing-chip">COGS reduction needed: ${fmtMoney(targetGaps.weekly_cogs_reduction_needed_cents)}</span>
        <span class="staffing-chip">Prime-cost reduction needed: ${fmtMoney(targetGaps.weekly_prime_cost_reduction_needed_cents)}</span>
        <span class="staffing-chip">Overhead absorption: ${fmtMoney(targetGaps.weekly_overhead_absorption_cents)}</span>
        <span class="staffing-chip">Revenue needed for prime target: ${fmtMoney(targetGaps.weekly_revenue_needed_for_prime_target_cents)}</span>
        <span class="staffing-chip">Revenue needed for margin target: ${fmtMoney(targetGaps.weekly_revenue_needed_for_net_margin_target_cents)}</span>
      </div>
      <div class="roi-headline" style="font-size:0.95rem; margin-top:0.75rem;">
        ${primaryLever.reason || ""}
      </div>
    `;
  }

  const top = Array.isArray(actions.top_proven_action_types) ? actions.top_proven_action_types : [];
  if (top.length === 0) {
    html += '<div class="no-data">No realized action outcomes yet. Adopt and log recommendations to build attribution memory.</div>';
  } else {
    html += `
      <table class="staffing-table">
        <thead>
          <tr>
            <th>Action Type</th>
            <th>Samples</th>
            <th>Avg Weekly Profit Delta</th>
            <th>Total Weekly Profit Delta</th>
            <th>Labor % Delta</th>
            <th>Rev/Labor Hr Delta</th>
          </tr>
        </thead>
        <tbody>
          ${top.map((r) => `
            <tr>
              <td>${typeLabel(r.action_type)}</td>
              <td>${r.realized_count ?? 0}</td>
              <td>${fmtMoney(r.avg_realized_weekly_profit_delta_cents)}</td>
              <td>${fmtMoney(r.total_realized_weekly_profit_delta_cents)}</td>
              <td>${fmtSigned(r.avg_realized_labor_pct_delta_pp, "pp")}</td>
              <td>${fmtSigned(r.avg_realized_rev_per_labor_hour_delta_pct, "%")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

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

function renderGapChip(label, cents) {
  if (cents == null || cents <= 0) return "";
  return `<span class="staffing-chip">${label}: ${fmtMoney(cents)}</span>`;
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
    const gate = (data && data.summary && data.summary.proven_gate) ? data.summary.proven_gate : null;
    if (gate && (gate.suppressed_count || 0) > 0) {
      const blocked = Array.isArray(gate.suppressed_action_types) ? gate.suppressed_action_types.join(", ") : "--";
      el.innerHTML = `
        <div class="efficiency-summary">
          <span class="staffing-chip">Proven gate suppressed: ${gate.suppressed_count}</span>
          <span class="staffing-chip">Blocked types: ${blocked || "--"}</span>
        </div>
        <div class="no-data">No actionable recommendations passed the proven-impact gate for this date.</div>
      `;
      return;
    }
    el.innerHTML = '<div class="no-data">No actionable recommendations yet for this date.</div>';
    return;
  }

  const summary = data.summary || {};
  const gate = summary.proven_gate || {};
  const goal = summary.profitability_goal || data.profitability_goal || {};
  const gaps = summary.profitability_gaps || data.profitability_gaps || {};
  let html = `
    <div class="efficiency-summary">
      <span class="staffing-chip">Generated: ${summary.actions_generated ?? data.actions.length}</span>
      <span class="staffing-chip">Phase: ${summary.optimization_phase || "--"}</span>
      <span class="staffing-chip">Proven-gate suppressed: ${gate.suppressed_count ?? 0}</span>
      <span class="staffing-chip">Data health: ${summary.data_health_status || "--"}</span>
    </div>
    ${summary.phase_reason ? `<div class="no-data">${summary.phase_reason}</div>` : ""}
  `;
  if (goal.focus || goal.reason) {
    html += `
      <div class="efficiency-summary">
        <span class="staffing-chip">Profitability focus: ${(goal.focus || "--").replace(/_/g, " ")}</span>
        ${renderGapChip("Labor gap", gaps.weekly_labor_reduction_needed_cents)}
        ${renderGapChip("COGS gap", gaps.weekly_cogs_reduction_needed_cents)}
        ${renderGapChip("Prime-cost gap", gaps.weekly_prime_cost_reduction_needed_cents)}
        ${renderGapChip("Revenue gap", gaps.weekly_revenue_needed_for_net_margin_target_cents)}
      </div>
      ${goal.reason ? `<div class="no-data">${goal.reason}</div>` : ""}
    `;
  }

  html += `<div class="next-actions-list">`;
  for (const action of data.actions) {
    const conf = action.confidence != null ? `${Math.round(action.confidence * 100)}%` : "--";
    const uplift = fmtMoney(action.expected_weekly_profit_uplift_cents);
    const alignment = action.profitability_alignment || {};
    html += `
      <div class="next-action-card">
        <div class="next-action-head">
          <div class="next-action-title">${action.title || action.action_type || "Action"}</div>
          <span class="status-pill status-balanced">${conf} confidence</span>
        </div>
        <div class="next-action-reason">${action.reason || "--"}</div>
        ${alignment.reason ? `<div class="next-action-reason"><strong>Profitability fit:</strong> ${alignment.reason}</div>` : ""}
        <div class="next-action-metrics">
          <span class="staffing-chip">Type: ${action.action_type || "--"}</span>
          <span class="staffing-chip">Est. weekly impact: ${uplift}</span>
          <span class="staffing-chip">Realized samples: ${action.realized_samples ?? 0}</span>
          ${alignment.focus_gap_label && alignment.focus_gap_cents > 0 ? `<span class="staffing-chip">${alignment.focus_gap_label}: ${fmtMoney(alignment.focus_gap_cents)}</span>` : ""}
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
  const profitability = data.profitability_context || summary.profitability_context || {};
  const profitabilityGaps = profitability.gaps || {};
  const profitabilityPrimary = profitability.primary_lever || {};
  const rows = data.days_plan || [];
  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip">Days with predictions: ${summary.days_with_predictions ?? "--"}</span>
      <span class="staffing-chip">Days without predictions: ${summary.days_without_predictions ?? "--"}</span>
      <span class="staffing-chip">Window: ${data.start_date || "--"} (+${data.days || "--"}d)</span>
    </div>
  `;
  if (profitabilityPrimary.focus || profitability.summary_note) {
    html += `
      <div class="staffing-summary">
        <span class="staffing-chip">Profit focus: ${(profitabilityPrimary.focus || "--").replace(/_/g, " ")}</span>
        <span class="staffing-chip">Weekly labor savings est: ${fmtMoney(profitability.estimated_weekly_labor_savings_cents)}</span>
        ${profitabilityGaps.weekly_labor_reduction_needed_cents > 0 ? `<span class="staffing-chip">Labor target: ${fmtMoney(profitabilityGaps.weekly_labor_reduction_needed_cents)}</span>` : ""}
        ${profitabilityGaps.weekly_revenue_needed_for_net_margin_target_cents > 0 ? `<span class="staffing-chip">Revenue gap: ${fmtMoney(profitabilityGaps.weekly_revenue_needed_for_net_margin_target_cents)}</span>` : ""}
      </div>
      ${profitability.summary_note ? `<div class="no-data">${profitability.summary_note}</div>` : ""}
    `;
  }
  html += `
    <table class="staffing-table">
      <thead>
        <tr><th>Date</th><th>Mode</th><th>Shifts</th><th>Hours</th><th>Labor Delta</th><th>Roles</th><th>Notes</th><th>Status</th></tr>
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
        <td>${row.notes || "--"}</td>
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
    if (c.source === "xero_financial_facts") notes = `connected=${c.connected ? "yes" : "no"}, days_14d=${c.days_14d ?? 0}`;
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

function renderInventoryAlerts(data) {
  const el = $("#inventory-alerts-content");
  const alerts = Array.isArray(data?.alerts) ? data.alerts : [];
  if (!data) {
    el.innerHTML = '<div class="no-data">Inventory alerts unavailable.</div>';
    return;
  }

  let html = `
    <div class="staffing-summary">
      <span class="staffing-chip">Alerts: ${data.alerts_total ?? alerts.length}</span>
      <span class="staffing-chip under">Warnings: ${data.warning_count ?? 0}</span>
      <span class="staffing-chip balance">Reorder soon: ${data.opportunity_count ?? 0}</span>
      <span class="staffing-chip">Lookback: ${data.lookback_days ?? "--"} days</span>
    </div>
  `;

  if (alerts.length === 0) {
    html += '<div class="no-data">No active stock alerts. Add inventory items, usage rules, and counts to enable tracking.</div>';
    html += '<div class="no-data">API: POST /analysis/inventory/items, POST /analysis/inventory/rules, POST /analysis/inventory/items/{inventory_item_id}/count</div>';
    html += '<div class="no-data">Quick start: POST /analysis/inventory/bootstrap-default-rules</div>';
    el.innerHTML = html;
    return;
  }

  const unitFmt = (n, unit) => (n == null ? "--" : `${Number(n).toFixed(1)} ${unit || "units"}`);
  const daysFmt = (n) => (n == null ? "--" : `${Number(n).toFixed(1)}d`);
  const scheduleFmt = (a) => {
    const delivery = a.next_delivery_date || "--";
    const cutoff = a.next_order_cutoff_at ? `Cutoff ${a.next_order_cutoff_at}` : null;
    return cutoff ? `${delivery}<div class="mini-meta">${cutoff}</div>` : delivery;
  };
  const timingFmt = (a) => {
    const projected = a.projected_on_hand_at_next_delivery == null
      ? null
      : `Proj ${Number(a.projected_on_hand_at_next_delivery).toFixed(1)} ${a.unit || "units"}`;
    const note = a.order_timing_note || "--";
    return projected ? `${note}<div class="mini-meta">${projected}</div>` : note;
  };
  const orderQtyFmt = (a) => {
    if (a.recommended_order_count != null && a.order_unit_name) {
      const label = a.recommended_order_count === 1 || String(a.order_unit_name).endsWith("s")
        ? a.order_unit_name
        : `${a.order_unit_name}s`;
      const baseUnits = a.recommended_order_quantity_units == null
        ? null
        : `${Number(a.recommended_order_quantity_units).toFixed(1)} ${a.unit || "units"}`;
      const source = a.order_profile_source ? `Source ${a.order_profile_source}` : null;
      const meta = [baseUnits, source].filter(Boolean).join(" · ");
      return meta
        ? `${a.recommended_order_count} ${label}<div class="mini-meta">${meta}</div>`
        : `${a.recommended_order_count} ${label}`;
    }
    if (a.recommended_order_note) {
      return `${unitFmt(a.recommended_reorder_units, a.unit)}<div class="mini-meta">${a.recommended_order_note}</div>`;
    }
    return unitFmt(a.recommended_reorder_units, a.unit);
  };

  html += `
    <table class="staffing-table">
      <thead>
        <tr>
          <th>Item</th>
          <th>Status</th>
          <th>On Hand</th>
          <th>Reorder Point</th>
          <th>Days Remaining</th>
          <th>Next Delivery</th>
          <th>Order Timing</th>
          <th>Order Qty</th>
        </tr>
      </thead>
      <tbody>
        ${alerts.map((a) => `
          <tr>
            <td>${a.item_name || "--"}</td>
            <td><span class="status-pill status-${a.severity === "warning" ? "red" : "yellow"}">${a.status || "--"}</span></td>
            <td>${unitFmt(a.effective_on_hand, a.unit)}</td>
            <td>${unitFmt(a.reorder_point, a.unit)}</td>
            <td>${daysFmt(a.days_remaining)}</td>
            <td>${scheduleFmt(a)}</td>
            <td>${timingFmt(a)}</td>
            <td>${orderQtyFmt(a)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

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

  // Fetch accuracy dashboard (30-day default)
  loadAccuracyDashboard(30);

  // Period selector click handler
  document.querySelectorAll(".period-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".period-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      loadAccuracyDashboard(parseInt(btn.dataset.days, 10));
    });
  });

  // Fetch weekly ROI summary
  fetchJSON(`${API}/analysis/weekly-roi`)
    .then(renderROI)
    .catch(() => renderROI(null));

  // Fetch bottom-line scorecard (profitability + realized impact memory)
  fetchJSON(`${API}/analysis/bottom-line-scorecard?days=30&compare_days=7`)
    .then(renderBottomLineScorecard)
    .catch(() => renderBottomLineScorecard(null));

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

  // Fetch inventory alerts
  fetchJSON(`${API}/analysis/inventory/alerts?lookback_days=21`)
    .then(renderInventoryAlerts)
    .catch(() => renderInventoryAlerts(null));

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
