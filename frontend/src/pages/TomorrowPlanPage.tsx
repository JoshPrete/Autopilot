import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { RushWindowCard } from "../components/RushWindowCard";
import { WeatherBadge } from "../components/WeatherBadge";
import type { TomorrowPlanResponse, TomorrowPlanLabor } from "../types/api";

// ── Helpers ────────────────────────────────────────────────────

function fmtRevenue(cents: number): string {
  if (!cents) return "—";
  return `$${(cents / 100).toLocaleString("en-AU", { maximumFractionDigits: 0 })}`;
}

function fmtDollars(cents: number): string {
  return `$${(cents / 100).toLocaleString("en-AU", { maximumFractionDigits: 0 })}`;
}

function confidenceClass(label: string | null): string {
  if (label === "high") return "conf-high";
  if (label === "low") return "conf-low";
  return "conf-med";
}

// ── Labor Badge ────────────────────────────────────────────────

function LaborBadge({ labor }: { labor: TomorrowPlanLabor }) {
  const riskClass = `labor-risk-${labor.labor_risk}`;
  const riskLabel = labor.labor_risk.toUpperCase();
  return (
    <div className={`labor-badge ${riskClass}`}>
      <span className="labor-badge-pct">{labor.wage_pct.toFixed(1)}%</span>
      <span className="labor-badge-label">wage</span>
      <span className={`labor-risk-pill risk-${labor.labor_risk}`}>{riskLabel}</span>
    </div>
  );
}

// ── Hourly Chart ───────────────────────────────────────────────

function HourlyChart({ hourly }: { hourly: TomorrowPlanResponse["hourly"] }) {
  const max = Math.max(...hourly.map((h) => h.predicted_workload ?? 0), 1);
  return (
    <div className="hourly-chart">
      {hourly.map((h) => (
        <div key={h.hour} className={`hourly-col ${h.is_rush ? "hourly-rush" : ""}`}>
          <div className="hourly-bar-wrap">
            <div
              className="hourly-bar-fill"
              style={{ height: `${Math.round(((h.predicted_workload ?? 0) / max) * 100)}%` }}
            />
          </div>
          <span className="hourly-label">{h.hour_label}</span>
        </div>
      ))}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────

export function TomorrowPlanPage() {
  const { user } = useAuth();
  const [plan, setPlan] = useState<TomorrowPlanResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) return;
    apiFetch<TomorrowPlanResponse>(`/api/sites/${user.site_id}/tomorrow-plan/json`)
      .then(setPlan)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [user]);

  if (loading) return <div className="loading">Loading tomorrow plan…</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!plan) return <div className="empty">No plan available.</div>;

  const { meta, forecast, labor, weather, actions, rush_windows, hourly, peak_trade } = plan;

  return (
    <div className="plan-page">

      {/* ── Header ── */}
      <div className="plan-header">
        <div className="plan-header-left">
          <h1 className="plan-day">{meta.day_name}</h1>
          <p className="plan-date">{meta.forecast_date}</p>
          <p className="plan-subtitle">Tomorrow Trade Plan</p>
        </div>
        <div className="plan-header-right">
          {meta.confidence_label && (
            <span className={`conf-badge ${confidenceClass(meta.confidence_label)}`}>
              {meta.confidence_label} confidence
              {meta.confidence != null && ` · ${Math.round(meta.confidence * 100)}%`}
            </span>
          )}
          <WeatherBadge weather={weather} />
        </div>
      </div>

      {/* ── KPI Row ── */}
      <div className="plan-kpis">
        <div className="plan-kpi">
          <span className="kpi-value">{fmtRevenue(forecast.predicted_revenue_cents)}</span>
          <span className="kpi-label">Forecast Revenue</span>
        </div>
        <div className="plan-kpi">
          <span className="kpi-value">{forecast.total_predicted_drinks ?? "—"}</span>
          <span className="kpi-label">Predicted Drinks</span>
        </div>
        <div className="plan-kpi">
          <span className="kpi-value">{meta.staff_scheduled ?? labor.staff_count}</span>
          <span className="kpi-label">Staff Rostered</span>
        </div>
        <div className="plan-kpi plan-kpi-labor">
          {labor && <LaborBadge labor={labor} />}
          <span className="kpi-label">
            Labour · {fmtDollars(labor.scheduled_labor_cents)} · {labor.total_hours}h
          </span>
        </div>
        {forecast.event_multiplier !== 1.0 && (
          <div className="plan-kpi">
            <span className="kpi-value">{forecast.event_multiplier.toFixed(1)}×</span>
            <span className="kpi-label">Event Multiplier</span>
          </div>
        )}
      </div>

      {/* ── Peak Trade Summary ── */}
      <section className="plan-section">
        <h2 className="plan-section-title">Trade Shape</h2>
        {peak_trade.vs_typical_direction && peak_trade.vs_typical_direction !== "similar" && (
          <div className={`vs-typical-banner vs-typical-${peak_trade.vs_typical_direction}`}>
            <span className="vs-typical-icon">
              {peak_trade.vs_typical_direction === "busier" ? "↑" : "↓"}
            </span>
            <span className="vs-typical-text">
              {Math.abs(peak_trade.vs_typical_pct ?? 0).toFixed(0)}%{" "}
              {peak_trade.vs_typical_direction} than a typical {meta.day_name}
            </span>
          </div>
        )}
        {peak_trade.vs_typical_direction === "similar" && (
          <div className="vs-typical-banner vs-typical-similar">
            <span className="vs-typical-text">Similar to a typical {meta.day_name}</span>
          </div>
        )}
        <div className="plan-summary-grid">
          <div className="plan-summary-card">
            <span className="summary-label">Busiest period</span>
            <span className="summary-value">
              {peak_trade.busiest_window_label ?? peak_trade.peak_hour_label ?? "—"}
            </span>
            <p className="summary-note">
              {peak_trade.busiest_window_drinks != null
                ? `${peak_trade.busiest_window_drinks} drinks expected through the strongest rush window.`
                : peak_trade.peak_hour_workload != null
                  ? `Peak hourly workload is ${peak_trade.peak_hour_workload.toFixed(1)} units.`
                  : "Peak trade timing is not available yet."}
            </p>
          </div>
          <div className="plan-summary-card">
            <span className="summary-label">Day shape</span>
            <span className="summary-value">{peak_trade.day_shape_title}</span>
            <p className="summary-note">{peak_trade.day_shape_note}</p>
          </div>
          <div className="plan-summary-card">
            <span className="summary-label">Peak / quiet hour</span>
            <span className="summary-value">
              {peak_trade.peak_hour_label ?? "—"} / {peak_trade.quiet_hour_label ?? "—"}
            </span>
            <p className="summary-note">
              {peak_trade.rush_window_count > 0
                ? `${peak_trade.rush_window_count} rush window${peak_trade.rush_window_count === 1 ? "" : "s"} detected for the day.`
                : "No rush windows detected, so staffing can stay flatter through trade."}
            </p>
          </div>
        </div>
      </section>

      {/* ── Actions ── */}
      {actions.length > 0 && (
        <section className="plan-section">
          <h2 className="plan-section-title">Operator Focus</h2>
          <ol className="plan-actions">
            {actions.map((action, i) => (
              <li key={i} className="plan-action-item">
                <span className="plan-action-num">{i + 1}</span>
                <span className="plan-action-text">{action}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* ── Rush Windows ── */}
      {rush_windows.length > 0 && (
        <section className="plan-section">
          <h2 className="plan-section-title">Peak Trade Windows</h2>
          <div className="rush-grid">
            {rush_windows.map((rw) => (
              <RushWindowCard key={rw.window_number} rush={rw} />
            ))}
          </div>
        </section>
      )}

      {/* ── Hourly Chart ── */}
      {hourly.length > 0 && (
        <section className="plan-section">
          <h2 className="plan-section-title">Expected Day Shape by Hour</h2>
          <HourlyChart hourly={hourly} />
        </section>
      )}

      <p className="plan-footer">
        Generated {meta.generated_at}
        {meta.prediction_id && ` · ${meta.prediction_id.slice(0, 8)}`}
      </p>
    </div>
  );
}
