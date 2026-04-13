import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  DataQualityFlag,
  DataQualityFlagsResponse,
  DayDiagnostics,
  PipelineStepResult,
} from "../types/api";

// ── Helpers ──────────────────────────────────────────────────

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function cents(v: number): string {
  return `$${(v / 100).toFixed(0)}`;
}

function flagTypeLabel(t: string): string {
  if (t === "partial_ingest") return "Partial Ingest";
  if (t === "manual_exclude_forecast") return "Manual Exclude";
  return t;
}

// ── Active Flags ─────────────────────────────────────────────

function FlagRow({
  flag,
  onCleared,
  siteId,
}: {
  flag: DataQualityFlag;
  onCleared: () => void;
  siteId: string;
}) {
  const [clearing, setClearing] = useState(false);
  const [err, setErr] = useState("");

  async function clear() {
    setClearing(true);
    setErr("");
    const endpoint =
      flag.flag_type === "partial_ingest"
        ? `/api/sites/${siteId}/analysis/data-quality/flags/partial-ingest?flag_date=${flag.flag_date}`
        : `/api/sites/${siteId}/analysis/data-quality/flags/manual-exclude?flag_date=${flag.flag_date}`;
    try {
      await apiFetch(endpoint, { method: "DELETE" });
      onCleared();
    } catch (e: unknown) {
      setErr((e as Error).message);
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className={`flag-row flag-sev-${flag.severity}`}>
      <div className="flag-main">
        <span className="flag-date">{flag.flag_date}</span>
        <span className={`flag-type-badge flag-type-${flag.flag_type}`}>
          {flagTypeLabel(flag.flag_type)}
        </span>
        <span className="flag-source">{flag.source}</span>
        {flag.reason && <span className="flag-reason">{flag.reason}</span>}
      </div>
      <div className="flag-actions">
        {err && <span className="flag-err">{err}</span>}
        <button className="btn-clear" onClick={clear} disabled={clearing}>
          {clearing ? "Clearing…" : "Clear Flag"}
        </button>
      </div>
    </div>
  );
}

// ── Day Diagnostics ───────────────────────────────────────────

function DiagnosticsPanel({
  diag,
}: {
  diag: DayDiagnostics;
}) {
  const { day, baseline_same_dow: bl } = diag;
  const ordPct =
    bl.median_completed_orders && bl.median_completed_orders > 0
      ? Math.round((day.completed_orders / bl.median_completed_orders) * 100)
      : null;
  const revPct =
    bl.median_completed_revenue_cents && bl.median_completed_revenue_cents > 0
      ? Math.round((day.completed_revenue_cents / bl.median_completed_revenue_cents) * 100)
      : null;

  return (
    <div className={`diag-panel ${diag.suspected_partial_ingest ? "diag-warn" : "diag-ok"}`}>
      <div className="diag-header">
        <strong>{diag.date}</strong>
        <span className={`diag-verdict ${diag.suspected_partial_ingest ? "verdict-warn" : "verdict-ok"}`}>
          {diag.suspected_partial_ingest ? "Suspected partial ingest" : "Looks complete"}
        </span>
      </div>
      <div className="diag-stats">
        <div className="diag-stat">
          <span className="diag-val">{day.completed_orders}</span>
          <span className="diag-label">Orders</span>
          {ordPct != null && (
            <span className={`diag-vs ${ordPct < 50 ? "diag-low" : ""}`}>{ordPct}% of baseline</span>
          )}
        </div>
        <div className="diag-stat">
          <span className="diag-val">{cents(day.completed_revenue_cents)}</span>
          <span className="diag-label">Revenue</span>
          {revPct != null && (
            <span className={`diag-vs ${revPct < 50 ? "diag-low" : ""}`}>{revPct}% of baseline</span>
          )}
        </div>
        <div className="diag-stat">
          <span className="diag-val">{day.active_hours}h</span>
          <span className="diag-label">Active Hours</span>
        </div>
        <div className="diag-stat">
          <span className="diag-val">{bl.sample_days}</span>
          <span className="diag-label">Baseline Days</span>
        </div>
      </div>
      {diag.signals.length > 0 && (
        <div className="diag-signals">
          {diag.signals.map((s) => (
            <span key={s} className="diag-signal">{s.replace(/_/g, " ")}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Step Result ───────────────────────────────────────────────

function StepResult({ label, result }: { label: string; result: PipelineStepResult }) {
  const ok = result.status === "ok";
  return (
    <div className={`step-result ${ok ? "step-ok" : "step-err"}`}>
      <div className="step-result-header">
        <strong>{label}</strong>
        <span className={`step-status ${ok ? "step-status-ok" : "step-status-err"}`}>
          {result.status}
        </span>
      </div>
      {ok && (
        <div className="step-result-stats">
          {result.orders != null && <span>{result.orders} orders</span>}
          {result.items != null && <span>{result.items} items</span>}
          {result.workload_units != null && (
            <span>{result.workload_units.toFixed(1)} WU</span>
          )}
          {result.quality_guard?.status === "flagged" && (
            <span className="step-flagged">⚠ Partial ingest flagged</span>
          )}
          {result.quality_guard?.dry_run === true && <span className="step-dry">dry run</span>}
        </div>
      )}
      {!ok && (
        <pre className="step-error-text">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────

export function AdminPage() {
  const { user } = useAuth();
  const sid = user?.site_id ?? "";

  // Flags
  const [flags, setFlags] = useState<DataQualityFlag[]>([]);
  const [flagsLoading, setFlagsLoading] = useState(true);
  const [flagsErr, setFlagsErr] = useState("");

  const loadFlags = useCallback(() => {
    setFlagsLoading(true);
    setFlagsErr("");
    apiFetch<DataQualityFlagsResponse>(
      `/api/sites/${sid}/analysis/data-quality/flags?active_only=true`
    )
      .then((r) => setFlags(r.flags))
      .catch((e) => setFlagsErr(e.message))
      .finally(() => setFlagsLoading(false));
  }, [sid]);

  useEffect(() => { loadFlags(); }, [loadFlags]);

  // Pipeline run
  const [runDate, setRunDate] = useState(todayIso());
  const [dryRun, setDryRun] = useState(false);
  const [running, setRunning] = useState<"ingest" | "predict" | null>(null);
  const [runResults, setRunResults] = useState<{ ingest?: PipelineStepResult; predict?: PipelineStepResult }>({});
  const [runErr, setRunErr] = useState("");

  async function runStep(step: "ingest" | "predict") {
    setRunning(step);
    setRunErr("");
    try {
      const result = await apiFetch<PipelineStepResult>(
        `/api/sites/${sid}/pipeline/${step}`,
        {
          method: "POST",
          body: JSON.stringify({ run_date: runDate, dry_run: dryRun }),
        }
      );
      setRunResults((prev) => ({ ...prev, [step]: result }));
      // Refresh flags after ingest (may create new flags)
      if (step === "ingest" && !dryRun) loadFlags();
    } catch (e: unknown) {
      setRunErr((e as Error).message);
    } finally {
      setRunning(null);
    }
  }

  // Day diagnostics
  const [diagDate, setDiagDate] = useState(todayIso());
  const [diag, setDiag] = useState<DayDiagnostics | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [diagErr, setDiagErr] = useState("");

  async function loadDiag() {
    setDiagLoading(true);
    setDiagErr("");
    setDiag(null);
    apiFetch<DayDiagnostics>(
      `/api/sites/${sid}/analysis/data-quality/day-diagnostics?target_date=${diagDate}`
    )
      .then(setDiag)
      .catch((e) => setDiagErr(e.message))
      .finally(() => setDiagLoading(false));
  }

  if (user?.role !== "MANAGER") {
    return <div className="error-banner">Manager access required.</div>;
  }

  return (
    <div className="admin-page">
      <h1 className="page-title">Admin</h1>

      {/* ── Diagnostic Tools ── */}
      <section className="admin-section">
        <h2>Diagnostic Tools</h2>
        <div className="admin-tool-links">
          <Link to="/pipeline" className="admin-tool-link">Pipeline Log</Link>
          <Link to="/accuracy" className="admin-tool-link">Forecast Accuracy</Link>
        </div>
      </section>

      {/* ── Active Flags ── */}
      <section className="admin-section">
        <div className="admin-section-header">
          <h2>Active Data Quality Flags</h2>
          <button className="btn-refresh" onClick={loadFlags} disabled={flagsLoading}>
            {flagsLoading ? "Loading…" : "Refresh"}
          </button>
        </div>
        {flagsErr && <div className="error-banner">{flagsErr}</div>}
        {!flagsLoading && flags.length === 0 && (
          <div className="admin-empty">No active flags — pipeline data looks clean.</div>
        )}
        {flags.map((f) => (
          <FlagRow key={f.flag_id} flag={f} siteId={sid} onCleared={loadFlags} />
        ))}
      </section>

      {/* ── Rerun Steps ── */}
      <section className="admin-section">
        <h2>Rerun Pipeline Step</h2>
        <div className="run-controls">
          <label className="run-label">
            Date
            <input
              type="date"
              className="run-date-input"
              value={runDate}
              onChange={(e) => setRunDate(e.target.value)}
            />
          </label>
          <label className="run-dry-label">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
            />
            Dry run
          </label>
          <button
            className="btn-run btn-run-ingest"
            onClick={() => runStep("ingest")}
            disabled={running !== null}
          >
            {running === "ingest" ? "Running Ingest…" : "Run Ingest"}
          </button>
          <button
            className="btn-run btn-run-predict"
            onClick={() => runStep("predict")}
            disabled={running !== null}
          >
            {running === "predict" ? "Running Predict…" : "Run Predict"}
          </button>
        </div>
        {runErr && <div className="error-banner" style={{ marginTop: "0.75rem" }}>{runErr}</div>}
        {runResults.ingest && (
          <StepResult label="Ingest Result" result={runResults.ingest} />
        )}
        {runResults.predict && (
          <StepResult label="Predict Result" result={runResults.predict} />
        )}
      </section>

      {/* ── Day Diagnostics ── */}
      <section className="admin-section">
        <h2>Day Diagnostics</h2>
        <div className="run-controls">
          <label className="run-label">
            Date
            <input
              type="date"
              className="run-date-input"
              value={diagDate}
              onChange={(e) => setDiagDate(e.target.value)}
            />
          </label>
          <button className="btn-run" onClick={loadDiag} disabled={diagLoading}>
            {diagLoading ? "Loading…" : "Check Day"}
          </button>
        </div>
        {diagErr && <div className="error-banner" style={{ marginTop: "0.75rem" }}>{diagErr}</div>}
        {diag && <DiagnosticsPanel diag={diag} />}
      </section>
    </div>
  );
}
