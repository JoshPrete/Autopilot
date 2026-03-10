import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { ScorecardResponse } from "../types/api";

function cents(v: number | null | undefined): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 100_000) return `${sign}$${(abs / 100_000).toFixed(1)}k`;
  return `${sign}$${(abs / 100).toFixed(0)}`;
}

function pct(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return `${v.toFixed(decimals)}%`;
}

function gapChip(label: string, value: number | null | undefined): string | null {
  if (value == null || value <= 0) return null;
  return `${label}: ${cents(value)}`;
}

function delta(v: number | null | undefined, positive_is_good = true): { label: string; cls: string } {
  if (v == null) return { label: "—", cls: "" };
  const good = positive_is_good ? v >= 0 : v <= 0;
  const sign = v > 0 ? "+" : "";
  return {
    label: `${sign}${v.toFixed(1)}%`,
    cls: good ? "delta-good" : "delta-bad",
  };
}

export function ScorecardPage() {
  const { user } = useAuth();
  const [data, setData] = useState<ScorecardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    apiFetch<ScorecardResponse>(`/api/sites/${user.site_id}/analysis/bottom-line-scorecard?days=30`)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  if (loading) return <div className="loading">Loading scorecard...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="empty">No data.</div>;

  const { kpis, trend, actions, window: win } = data;
  const dirs = trend.directions ?? {};
  const deltas = trend.deltas ?? {};
  const targets = data.targets ?? null;
  const current = targets?.current ?? {};
  const targetBands = targets?.targets ?? {};
  const gaps = targets?.gaps ?? null;
  const primaryLever = targets?.primary_lever ?? null;

  const laborDelta = delta(typeof deltas.labor_pct === "number" ? deltas.labor_pct : null, false);
  const revDelta = delta(typeof deltas.revenue_cents === "number" ? deltas.revenue_cents : null, true);
  const profitDelta = delta(typeof deltas.net_profit_cents === "number" ? deltas.net_profit_cents : null, true);
  const activeGaps = [
    gapChip("Labor gap", gaps?.weekly_labor_reduction_needed_cents),
    gapChip("COGS gap", gaps?.weekly_cogs_reduction_needed_cents),
    gapChip("Prime-cost gap", gaps?.weekly_prime_cost_reduction_needed_cents),
    gapChip("Overhead absorption", gaps?.weekly_overhead_absorption_cents),
    gapChip("Revenue gap", gaps?.weekly_revenue_needed_for_net_margin_target_cents),
  ].filter(Boolean) as string[];

  return (
    <div className="scorecard-page">
      <div className="page-header">
        <h1 className="page-title">Bottom Line Scorecard</h1>
        <span className="page-subtitle">
          {win.start_date} – {win.end_date} &middot; {win.days} days
        </span>
      </div>

      {data.headline && <p className="scorecard-headline">{data.headline}</p>}

      <section className="scorecard-kpis">
        <div className="kpi-card">
          <span className="kpi-value">{cents(kpis.revenue_cents)}</span>
          <span className="kpi-label">Revenue</span>
          {dirs.revenue_cents && <span className={`kpi-dir ${dirs.revenue_cents}`}>{revDelta.label}</span>}
        </div>
        <div className="kpi-card">
          <span className="kpi-value">{cents(kpis.net_profit_cents)}</span>
          <span className="kpi-label">Net Profit</span>
          {dirs.net_profit_cents && <span className={`kpi-dir ${dirs.net_profit_cents}`}>{profitDelta.label}</span>}
        </div>
        <div className="kpi-card">
          <span className="kpi-value">{pct(kpis.labor_pct)}</span>
          <span className="kpi-label">Labor %</span>
          {dirs.labor_pct && <span className={`kpi-dir ${dirs.labor_pct}`}>{laborDelta.label}</span>}
        </div>
        <div className="kpi-card">
          <span className="kpi-value">{cents(kpis.labor_cost_cents)}</span>
          <span className="kpi-label">Labor Cost</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-value">{cents(kpis.cogs_cents)}</span>
          <span className="kpi-label">COGS</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-value">{kpis.revenue_per_labor_hour != null ? `$${kpis.revenue_per_labor_hour.toFixed(0)}/hr` : "—"}</span>
          <span className="kpi-label">Rev / Labor Hr</span>
        </div>
      </section>

      {(primaryLever?.focus || activeGaps.length > 0) && (
        <section className="scorecard-section">
          <h2>Profitability Target Gap</h2>
          <div className="target-gap-card">
            <div className="target-gap-header">
              {primaryLever?.focus && (
                <span className="focus-pill">{primaryLever.focus.replace(/_/g, " ")}</span>
              )}
              {primaryLever?.reason && (
                <span className="target-gap-reason">{primaryLever.reason}</span>
              )}
            </div>
            <div className="target-gap-metrics">
              <div className="target-gap-metric">
                <span className="target-gap-label">Labor</span>
                <span className="target-gap-value">
                  {pct(typeof current.labor_pct === "number" ? current.labor_pct : null)} / target ≤{" "}
                  {pct(typeof targetBands.labor_pct_high === "number" ? targetBands.labor_pct_high : null, 0)}
                </span>
              </div>
              <div className="target-gap-metric">
                <span className="target-gap-label">COGS</span>
                <span className="target-gap-value">
                  {pct(typeof current.cogs_pct === "number" ? current.cogs_pct : null)} / target ≤{" "}
                  {pct(typeof targetBands.cogs_pct_high === "number" ? targetBands.cogs_pct_high : null, 0)}
                </span>
              </div>
              <div className="target-gap-metric">
                <span className="target-gap-label">Prime Cost</span>
                <span className="target-gap-value">
                  {pct(typeof current.prime_cost_pct === "number" ? current.prime_cost_pct : null)} / target ≤{" "}
                  {pct(typeof targetBands.prime_cost_pct_high === "number" ? targetBands.prime_cost_pct_high : null, 0)}
                </span>
              </div>
              <div className="target-gap-metric">
                <span className="target-gap-label">Margin Basis</span>
                <span className="target-gap-value">
                  {pct(typeof current.margin_basis_net_margin_pct === "number" ? current.margin_basis_net_margin_pct : null)}{" "}
                  / {typeof current.margin_basis_source === "string" ? current.margin_basis_source : "operational_proxy"}
                </span>
              </div>
              <div className="target-gap-metric">
                <span className="target-gap-label">Overhead Proxy</span>
                <span className="target-gap-value">
                  {cents(typeof current.operating_overhead_cents === "number" ? current.operating_overhead_cents : null)}
                </span>
              </div>
            </div>
            {activeGaps.length > 0 && (
              <div className="profitability-gap-list">
                {activeGaps.map((label) => (
                  <span key={label} className="profitability-gap-chip">{label}</span>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      <section className="scorecard-section">
        <h2>Recommendation Impact</h2>
        <div className="rec-stats">
          <div className="rec-stat">
            <span className="rec-val">{actions.recommendations_adopted} / {actions.recommendations_generated}</span>
            <span className="rec-label">Adopted</span>
          </div>
          <div className="rec-stat">
            <span className="rec-val">
              {actions.adoption_rate != null ? pct(actions.adoption_rate * 100, 0) : "—"}
            </span>
            <span className="rec-label">Adoption Rate</span>
          </div>
          <div className="rec-stat">
            <span className="rec-val">{actions.realized_actions}</span>
            <span className="rec-label">Realized Actions</span>
          </div>
          <div className="rec-stat">
            <span className="rec-val">
              {cents(actions.avg_realized_weekly_profit_delta_cents)}/wk
            </span>
            <span className="rec-label">Avg Weekly Profit Delta</span>
          </div>
        </div>
        {actions.top_proven_action_types.length > 0 && (
          <div className="proven-actions">
            <span className="proven-label">Top proven actions: </span>
            {actions.top_proven_action_types.map((t) => (
              <span key={t.action_type} className="action-tag">
                {t.action_type.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
