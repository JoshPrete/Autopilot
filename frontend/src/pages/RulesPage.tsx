import { useEffect, useState, useCallback } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";

// ── Types ──────────────────────────────────────────────────────

interface OperatorRule {
  rule_id: string;
  rule_type: string;
  rule_name: string;
  payload: Record<string, unknown>;
  source: string;
  status: "proposed" | "confirmed" | "rejected";
  confidence: number | null;
  active: boolean;
  created_by: string | null;
  confirmed_by: string | null;
  created_at: string;
  confirmed_at: string | null;
  updated_at: string;
}

interface RulesResponse {
  rules: OperatorRule[];
  count: number;
}

// ── Helpers ────────────────────────────────────────────────────

function ruleTypeLabel(t: string): string {
  const labels: Record<string, string> = {
    delivery_schedule: "Delivery",
    ordering_schedule: "Ordering",
    storage_rule: "Storage",
    recipe_definition: "Recipe",
  };
  return labels[t] ?? t;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-AU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function confidenceColor(c: number | null): string {
  if (c == null) return "conf-med";
  if (c >= 0.8) return "conf-high";
  if (c >= 0.5) return "conf-med";
  return "conf-low";
}

// ── RuleCard ───────────────────────────────────────────────────

function RuleCard({
  rule,
  siteId,
  canManage,
  onUpdate,
}: {
  rule: OperatorRule;
  siteId: string;
  canManage: boolean;
  onUpdate: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function act(action: "confirm" | "reject") {
    setBusy(true);
    setErr("");
    try {
      await apiFetch(`/api/sites/${siteId}/rules/${rule.rule_id}/${action}`, {
        method: "PUT",
      });
      onUpdate();
    } catch (e: unknown) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const statusClass =
    rule.status === "confirmed"
      ? "rule-status-confirmed"
      : rule.status === "rejected"
        ? "rule-status-rejected"
        : "rule-status-proposed";

  return (
    <div className={`rule-card rule-${rule.status}`}>
      <div className="rule-card-header">
        <div className="rule-card-title">
          <span className="rule-type-badge">{ruleTypeLabel(rule.rule_type)}</span>
          <span className="rule-name">{rule.rule_name}</span>
        </div>
        <span className={`rule-status ${statusClass}`}>{rule.status}</span>
      </div>

      {rule.confidence != null && (
        <span className={`rule-confidence ${confidenceColor(rule.confidence)}`}>
          {Math.round(rule.confidence * 100)}% confidence
        </span>
      )}

      <div className="rule-payload">
        {Object.entries(rule.payload).map(([k, v]) => (
          <div key={k} className="rule-payload-row">
            <span className="rule-payload-key">{k.replace(/_/g, " ")}</span>
            <span className="rule-payload-val">{String(v)}</span>
          </div>
        ))}
      </div>

      <div className="rule-meta">
        <span>Source: {rule.source}</span>
        {rule.created_by && <span>By: {rule.created_by}</span>}
        <span>Created: {formatDate(rule.created_at)}</span>
        {rule.confirmed_by && <span>Confirmed by: {rule.confirmed_by}</span>}
      </div>

      {err && <p className="rule-err">{err}</p>}

      {canManage && rule.status === "proposed" && (
        <div className="rule-actions">
          <button
            className="btn-confirm"
            onClick={() => act("confirm")}
            disabled={busy}
          >
            {busy ? "…" : "Confirm"}
          </button>
          <button
            className="btn-reject"
            onClick={() => act("reject")}
            disabled={busy}
          >
            {busy ? "…" : "Reject"}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────

export function RulesPage() {
  const { user } = useAuth();
  const [rules, setRules] = useState<OperatorRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const canManage = user?.role === "MANAGER" || user?.role === "P1";

  const load = useCallback(() => {
    if (!user) return;
    setLoading(true);
    setError("");
    const qs = statusFilter !== "all" ? `?status=${statusFilter}` : "?active_only=false";
    apiFetch<RulesResponse>(`/api/sites/${user.site_id}/rules${qs}`)
      .then((d) => setRules(d.rules))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const proposed = rules.filter((r) => r.status === "proposed");
  const confirmed = rules.filter((r) => r.status === "confirmed");
  const rejected = rules.filter((r) => r.status === "rejected");

  return (
    <div className="page-rules">
      <div className="page-header">
        <h1>Operating Rules</h1>
        <p className="page-subtitle">
          Knowledge captured from chat — confirm to make rules active.
        </p>
      </div>

      <div className="rules-filters">
        {["all", "proposed", "confirmed", "rejected"].map((s) => (
          <button
            key={s}
            className={`filter-btn ${statusFilter === s ? "active" : ""}`}
            onClick={() => setStatusFilter(s)}
          >
            {s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
            {s === "proposed" && proposed.length > 0 && (
              <span className="filter-badge">{proposed.length}</span>
            )}
          </button>
        ))}
      </div>

      {loading && <p className="loading-msg">Loading rules…</p>}
      {error && <p className="error-msg">{error}</p>}

      {!loading && !error && (
        <>
          {(statusFilter === "all" || statusFilter === "proposed") && proposed.length > 0 && (
            <section className="rules-section">
              <h2 className="rules-section-title">Pending Review ({proposed.length})</h2>
              <div className="rules-grid">
                {proposed.map((r) => (
                  <RuleCard
                    key={r.rule_id}
                    rule={r}
                    siteId={user!.site_id}
                    canManage={canManage}
                    onUpdate={load}
                  />
                ))}
              </div>
            </section>
          )}

          {(statusFilter === "all" || statusFilter === "confirmed") && confirmed.length > 0 && (
            <section className="rules-section">
              <h2 className="rules-section-title">Active Rules ({confirmed.length})</h2>
              <div className="rules-grid">
                {confirmed.map((r) => (
                  <RuleCard
                    key={r.rule_id}
                    rule={r}
                    siteId={user!.site_id}
                    canManage={canManage}
                    onUpdate={load}
                  />
                ))}
              </div>
            </section>
          )}

          {(statusFilter === "all" || statusFilter === "rejected") && rejected.length > 0 && (
            <section className="rules-section">
              <h2 className="rules-section-title">Rejected ({rejected.length})</h2>
              <div className="rules-grid">
                {rejected.map((r) => (
                  <RuleCard
                    key={r.rule_id}
                    rule={r}
                    siteId={user!.site_id}
                    canManage={canManage}
                    onUpdate={load}
                  />
                ))}
              </div>
            </section>
          )}

          {rules.length === 0 && (
            <div className="rules-empty">
              <p>No rules yet.</p>
              <p className="rules-empty-hint">
                Start a chat conversation — any delivery schedules, ordering rules, or recipes
                you describe will be proposed here for review.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
