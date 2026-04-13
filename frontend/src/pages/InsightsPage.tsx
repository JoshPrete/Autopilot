import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { NextActionsResponse, NextAction } from "../types/api";

interface Hypothesis {
  hypothesis_key: string;
  category: string;
  title: string;
  statement: string;
  implication: string;
  confidence: number;
  confidence_label: "strong" | "moderate" | "weak";
  evidence: Record<string, unknown>;
}

interface BusinessIntelligenceResponse {
  generated_at: string;
  window_days: number;
  data_days: number;
  hypotheses: Hypothesis[];
  summary: { strong: number; moderate: number; weak: number; total: number };
}

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

function humanizeToken(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function gapSentence(label: string, cents: number | null | undefined): string | null {
  if (!cents || cents <= 0) return null;
  return `${label} needs to improve by about ${formatCents(cents)} per week.`;
}

function upliftSentence(action: NextAction): string {
  if (action.proven_weekly_impact_cents != null) {
    return `This has already shown about ${formatCents(action.proven_weekly_impact_cents)} per week when adopted.`;
  }
  if (action.expected_weekly_profit_uplift_cents != null) {
    return `Estimated upside is about ${formatCents(action.expected_weekly_profit_uplift_cents)} per week.`;
  }
  return "No weekly impact estimate is available yet.";
}

function opportunityFocusLabel(action: NextAction): string | null {
  const label = action.profitability_alignment?.focus_gap_label;
  if (!label) return null;
  return `${humanizeToken(label)} pressure`;
}

function ActionCard({ action, siteId }: { action: NextAction; siteId: string }) {
  const [feedback, setFeedback] = useState<FeedbackState>("idle");
  const confCls = confidenceClass(action.confidence);
  const alignment = action.profitability_alignment;
  const focusGap = opportunityFocusLabel(action);

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
      <div className="action-block">
        <span className="action-block-label">Do this</span>
        <p className="action-reason">{action.reason}</p>
      </div>
      <div className="action-block">
        <span className="action-block-label">Why now</span>
        <div className="action-alignment">
          {alignment?.reason ?? "This is the most relevant move for the current profitability picture."}
        </div>
      </div>
      <div className="action-block">
        <span className="action-block-label">Expected result</span>
        <p className="action-expected">{upliftSentence(action)}</p>
      </div>
      <div className="action-meta">
        {action.proven_weekly_impact_cents != null && (
          <span className="action-stat proven">
            Proven <strong>{formatCents(action.proven_weekly_impact_cents)}/wk</strong>
          </span>
        )}
        {action.expected_weekly_profit_uplift_cents != null && (
          <span className="action-stat">
            Est. <strong>{formatCents(action.expected_weekly_profit_uplift_cents)}/wk</strong>
          </span>
        )}
        {action.optimization_phase && (
          <span className="action-phase">{humanizeToken(action.optimization_phase)}</span>
        )}
        {focusGap && <span className="action-gap">{focusGap}</span>}
        {action.realized_samples > 0 && (
          <span className="action-stat">Real-world samples <strong>{action.realized_samples}</strong></span>
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

const CATEGORY_LABELS: Record<string, string> = {
  revenue_pattern: "Revenue",
  labour_efficiency: "Labour",
  product_mix: "Product mix",
  cost_trend: "Costs",
};

const CONF_COLORS: Record<string, string> = {
  strong: "#22c55e",
  moderate: "#f59e0b",
  weak: "#9ca3af",
};

function HypothesisCard({ h }: { h: Hypothesis }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="hypothesis-card" onClick={() => setExpanded((e) => !e)}>
      <div className="hypothesis-card-header">
        <span className="hypothesis-category">{CATEGORY_LABELS[h.category] ?? h.category}</span>
        <span
          className="hypothesis-conf-dot"
          style={{ background: CONF_COLORS[h.confidence_label] }}
          title={`${h.confidence_label} confidence (${Math.round(h.confidence * 100)}%)`}
        />
      </div>
      <p className="hypothesis-statement">{h.statement}</p>
      {expanded && (
        <p className="hypothesis-implication">{h.implication}</p>
      )}
    </div>
  );
}

export function InsightsPage() {
  const { user } = useAuth();
  const [data, setData] = useState<NextActionsResponse | null>(null);
  const [bi, setBi] = useState<BusinessIntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    Promise.all([
      apiFetch<NextActionsResponse>(
        `/api/sites/${user.site_id}/analysis/recommendations/next-actions`
      ),
      apiFetch<BusinessIntelligenceResponse>(
        `/api/sites/${user.site_id}/analysis/business-intelligence`
      ).catch(() => null),
    ])
      .then(([actions, biData]) => {
        setData(actions);
        setBi(biData);
      })
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
    gapSentence("Labour", gaps?.weekly_labor_reduction_needed_cents),
    gapSentence("COGS", gaps?.weekly_cogs_reduction_needed_cents),
    gapSentence("Prime cost", gaps?.weekly_prime_cost_reduction_needed_cents),
    gapSentence("Revenue", gaps?.weekly_revenue_needed_for_net_margin_target_cents),
  ].filter(Boolean) as string[];
  const topAction = data.actions[0] ?? null;

  return (
    <div className="insights-page">
      <div className="page-header">
        <h1 className="page-title">Profit Opportunities</h1>
        {summary?.optimization_phase && (
          <span className="page-subtitle">Mode: {humanizeToken(summary.optimization_phase)}</span>
        )}
      </div>

      <p className="phase-reason">
        Plain-English view of where profit is leaking and the simplest next moves to improve it.
      </p>

      {summary?.phase_reason && <p className="phase-reason">{summary.phase_reason}</p>}

      {(goal?.focus || goal?.reason || activeGaps.length > 0 || topAction) && (
        <section className="profitability-focus-card">
          <div className="opportunity-summary-grid">
            <div className="opportunity-summary-card">
              <span className="opportunity-summary-label">Main focus</span>
              <strong className="opportunity-summary-value">{humanizeToken(goal?.focus)}</strong>
              <p className="opportunity-summary-note">
                {goal?.reason ?? summary?.phase_reason ?? "This is the main area where the system sees profit pressure right now."}
              </p>
            </div>
            <div className="opportunity-summary-card">
              <span className="opportunity-summary-label">Best first move</span>
              <strong className="opportunity-summary-value">{topAction?.title ?? "No clear first move yet"}</strong>
              <p className="opportunity-summary-note">
                {topAction ? upliftSentence(topAction) : "Run the intelligence step to generate ranked opportunities."}
              </p>
            </div>
            <div className="opportunity-summary-card">
              <span className="opportunity-summary-label">What to watch</span>
              <strong className="opportunity-summary-value">
                {activeGaps[0] ? "Biggest current gap" : "Data trust"}
              </strong>
              <p className="opportunity-summary-note">
                {activeGaps[0] ?? `Current data health is ${summary?.data_health_status ?? "unknown"}.`}
              </p>
            </div>
          </div>
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

      {bi && bi.hypotheses.length > 0 && (
        <section className="business-profile-section">
          <div className="business-profile-header">
            <h2 className="business-profile-title">What we know about this business</h2>
            <span className="business-profile-meta">
              {bi.summary.strong} strong · {bi.summary.moderate} moderate · last {bi.window_days} days
            </span>
          </div>
          <div className="hypothesis-grid">
            {bi.hypotheses
              .filter((h) => h.confidence_label !== "weak")
              .slice(0, 6)
              .map((h) => (
                <HypothesisCard key={h.hypothesis_key} h={h} />
              ))}
          </div>
        </section>
      )}

      {data.actions.length === 0 ? (
        <div className="empty">No opportunities generated yet. Run the intelligence step to populate.</div>
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
