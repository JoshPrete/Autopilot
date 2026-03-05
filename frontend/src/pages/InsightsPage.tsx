import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { NextActionsResponse, NextAction } from "../types/api";

function formatCents(v: number | null | undefined): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 100_000) return `${sign}$${(abs / 100_000).toFixed(1)}k`;
  return `${sign}$${(abs / 100).toFixed(0)}`;
}

function confidenceClass(c: number): string {
  if (c >= 0.7) return "conf-high";
  if (c >= 0.45) return "conf-med";
  return "conf-low";
}

function ActionCard({ action }: { action: NextAction }) {
  const confCls = confidenceClass(action.confidence);
  return (
    <div className="action-card">
      <div className="action-card-header">
        <span className="action-title">{action.title}</span>
        <span className={`action-conf ${confCls}`}>{Math.round(action.confidence * 100)}% conf</span>
      </div>
      <p className="action-reason">{action.reason}</p>
      <div className="action-meta">
        {action.expected_weekly_profit_uplift_cents != null && (
          <span className="action-stat">
            Est. <strong>{formatCents(action.expected_weekly_profit_uplift_cents)}/wk</strong>
          </span>
        )}
        {action.proven_weekly_impact_cents != null && (
          <span className="action-stat proven">
            Proven <strong>{formatCents(action.proven_weekly_impact_cents)}/wk</strong>
          </span>
        )}
        {action.optimization_phase && (
          <span className="action-phase">{action.optimization_phase}</span>
        )}
      </div>
    </div>
  );
}

export function InsightsPage() {
  const { user } = useAuth();
  const [data, setData] = useState<NextActionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    apiFetch<NextActionsResponse>(
      `/api/sites/${user.site_id}/analysis/recommendations/next-actions`
    )
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  if (loading) return <div className="loading">Loading insights...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="empty">No insights available.</div>;

  return (
    <div className="insights-page">
      <div className="page-header">
        <h1 className="page-title">Next Actions</h1>
        {data.optimization_phase && (
          <span className="page-subtitle">Phase: {data.optimization_phase}</span>
        )}
      </div>

      {data.phase_reason && (
        <p className="phase-reason">{data.phase_reason}</p>
      )}

      {data.actions.length === 0 ? (
        <div className="empty">No actions generated yet. Run the intelligence step to populate.</div>
      ) : (
        <div className="action-list">
          {data.actions.map((a) => (
            <ActionCard key={a.action_key} action={a} />
          ))}
        </div>
      )}
    </div>
  );
}
