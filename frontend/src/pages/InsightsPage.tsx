import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { NextActionsResponse, NextAction } from "../types/api";

type FeedbackState = "idle" | "busy" | "done_adopted" | "done_skipped" | "error";

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

function gapLabel(label: string | null | undefined, cents: number | null | undefined): string | null {
  if (!label || !cents || cents <= 0) return null;
  return `${label}: ${formatCents(cents)}`;
}

function ActionCard({ action, siteId }: { action: NextAction; siteId: string }) {
  const [feedback, setFeedback] = useState<FeedbackState>("idle");
  const confCls = confidenceClass(action.confidence);
  const alignment = action.profitability_alignment;
  const focusGap = gapLabel(alignment?.focus_gap_label, alignment?.focus_gap_cents);

  async function sendFeedback(adopted: boolean) {
    if (!action.rec_id || feedback === "busy") return;
    setFeedback("busy");
    try {
      await apiFetch(
        `/api/sites/${siteId}/analysis/recommendations/feedback?rec_id=${action.rec_id}&adopted=${adopted}`,
        { method: "POST" }
      );
      setFeedback(adopted ? "done_adopted" : "done_skipped");
    } catch {
      setFeedback("error");
    }
  }

  return (
    <div className={`action-card ${feedback.startsWith("done") ? "action-card-done" : ""}`}>
      <div className="action-card-header">
        <span className="action-title">{action.title}</span>
        <span className={`action-conf ${confCls}`}>{Math.round(action.confidence * 100)}% conf</span>
      </div>
      <p className="action-reason">{action.reason}</p>
      {alignment?.reason && (
        <div className="action-alignment">
          <strong>Profitability fit:</strong> {alignment.reason}
        </div>
      )}
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
        {focusGap && <span className="action-gap">{focusGap}</span>}
        {action.realized_samples > 0 && (
          <span className="action-stat">Realized samples <strong>{action.realized_samples}</strong></span>
        )}
      </div>

      {action.rec_id && (
        <div className="action-feedback">
          {feedback === "idle" && (
            <>
              <button className="btn-adopted" onClick={() => sendFeedback(true)}>Done</button>
              <button className="btn-skipped" onClick={() => sendFeedback(false)}>Skipped</button>
            </>
          )}
          {feedback === "busy" && <span className="feedback-saving">Saving…</span>}
          {feedback === "done_adopted" && <span className="feedback-tag feedback-adopted">✓ Done</span>}
          {feedback === "done_skipped" && <span className="feedback-tag feedback-skipped">Skipped</span>}
          {feedback === "error" && <span className="feedback-tag feedback-error">Failed — try again</span>}
        </div>
      )}
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

  const summary = data.summary;
  const goal = summary?.profitability_goal ?? data.profitability_goal;
  const gaps = summary?.profitability_gaps ?? data.profitability_gaps;
  const activeGaps = [
    gapLabel("Labor gap", gaps?.weekly_labor_reduction_needed_cents),
    gapLabel("COGS gap", gaps?.weekly_cogs_reduction_needed_cents),
    gapLabel("Prime-cost gap", gaps?.weekly_prime_cost_reduction_needed_cents),
    gapLabel("Revenue gap", gaps?.weekly_revenue_needed_for_net_margin_target_cents),
  ].filter(Boolean) as string[];

  return (
    <div className="insights-page">
      <div className="page-header">
        <h1 className="page-title">Next Actions</h1>
        {summary?.optimization_phase && (
          <span className="page-subtitle">Phase: {summary.optimization_phase}</span>
        )}
      </div>

      {summary?.phase_reason && (
        <p className="phase-reason">{summary.phase_reason}</p>
      )}

      {(goal?.focus || goal?.reason || activeGaps.length > 0) && (
        <section className="profitability-focus-card">
          <div className="profitability-focus-header">
            <h2>Profitability Focus</h2>
            {goal?.focus && <span className="focus-pill">{goal.focus.replace(/_/g, " ")}</span>}
          </div>
          {goal?.reason && <p className="profitability-focus-reason">{goal.reason}</p>}
          {activeGaps.length > 0 && (
            <div className="profitability-gap-list">
              {activeGaps.map((gap) => (
                <span key={gap} className="profitability-gap-chip">{gap}</span>
              ))}
            </div>
          )}
          {summary?.data_health_status && (
            <div className="profitability-focus-footer">
              Data health: <strong>{summary.data_health_status}</strong>
            </div>
          )}
        </section>
      )}

      {data.actions.length === 0 ? (
        <div className="empty">No actions generated yet. Run the intelligence step to populate.</div>
      ) : (
        <div className="action-list">
          {data.actions.map((a) => (
            <ActionCard key={a.action_key} action={a} siteId={user!.site_id} />
          ))}
        </div>
      )}
    </div>
  );
}
