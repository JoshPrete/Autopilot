import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  DailyLoopStatus,
  PipelineHealth,
  PipelineRunsResponse,
  PipelineRun,
} from "../types/api";

function fmtAt(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  return d.toLocaleDateString();
}

function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function StatusDot({ status }: { status: string }) {
  const cls =
    status === "ok" || status === "green"
      ? "dot-green"
      : status === "yellow"
      ? "dot-yellow"
      : status === "error" || status === "red"
      ? "dot-red"
      : "dot-grey";
  return <span className={`status-dot ${cls}`} />;
}

function ReadinessBanner({ readiness }: { readiness: "green" | "yellow" | "red" }) {
  const labels = { green: "All systems go", yellow: "Running with warnings", red: "Pipeline needs attention" };
  return (
    <div className={`readiness-banner readiness-${readiness}`}>
      <StatusDot status={readiness} />
      <span>{labels[readiness]}</span>
    </div>
  );
}

function RunRow({ run }: { run: PipelineRun }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr
        className={`run-row run-${run.status}${run.error_text ? " run-clickable" : ""}`}
        onClick={() => run.error_text && setExpanded((v) => !v)}
      >
        <td><StatusDot status={run.status} /></td>
        <td className="run-job">{run.job_name}</td>
        <td className="run-time">{fmtAt(run.started_at)}</td>
        <td className="run-dur">{fmtDuration(run.duration_ms)}</td>
        <td className="run-status-label">{run.status}{run.error_text ? " ▾" : ""}</td>
      </tr>
      {expanded && run.error_text && (
        <tr className="run-error-row">
          <td colSpan={5}>
            <pre className="run-error-text">{run.error_text}</pre>
          </td>
        </tr>
      )}
    </>
  );
}

export function PipelinePage() {
  const { user } = useAuth();
  const [loop, setLoop] = useState<DailyLoopStatus | null>(null);
  const [health, setHealth] = useState<PipelineHealth | null>(null);
  const [runs, setRuns] = useState<PipelineRunsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    const sid = user.site_id;
    Promise.all([
      apiFetch<DailyLoopStatus>(`/api/sites/${sid}/analysis/daily-loop-status`),
      apiFetch<PipelineHealth>(`/api/sites/${sid}/analysis/pipeline-health?hours=24`),
      apiFetch<PipelineRunsResponse>(`/api/sites/${sid}/analysis/pipeline-runs?limit=30`),
    ])
      .then(([l, h, r]) => {
        setLoop(l);
        setHealth(h);
        setRuns(r);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  if (loading) return <div className="loading">Loading pipeline status...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!loop) return <div className="empty">No pipeline data.</div>;

  const lastIngest = loop.last_runs["ingest"];
  const lastPredict = loop.last_runs["predict"];
  const lastDailyLoop = loop.last_runs["daily_loop"];

  return (
    <div className="pipeline-page">
      <div className="page-header">
        <h1 className="page-title">Pipeline Status</h1>
        <span className="page-subtitle">checked {fmtAt(loop.checked_at)}</span>
      </div>

      <ReadinessBanner readiness={loop.readiness} />

      <div className="pipeline-cards">
        {/* Ingest */}
        <div className={`pipeline-card ${lastIngest?.status === "ok" ? "card-ok" : "card-warn"}`}>
          <div className="pipeline-card-title">
            <StatusDot status={lastIngest?.status ?? "unknown"} />
            Ingest
          </div>
          <div className="pipeline-card-detail">{fmtAt(lastIngest?.at)}</div>
          {lastIngest?.duration_ms && (
            <div className="pipeline-card-meta">{fmtDuration(lastIngest.duration_ms)}</div>
          )}
          {lastIngest?.error && (
            <div className="pipeline-card-error">{lastIngest.error}</div>
          )}
        </div>

        {/* Predict */}
        <div className={`pipeline-card ${lastPredict?.status === "ok" || loop.tomorrow ? "card-ok" : "card-warn"}`}>
          <div className="pipeline-card-title">
            <StatusDot status={lastPredict?.status ?? (loop.tomorrow ? "ok" : "unknown")} />
            Predict
          </div>
          <div className="pipeline-card-detail">{fmtAt(lastPredict?.at)}</div>
          {lastPredict?.duration_ms && (
            <div className="pipeline-card-meta">{fmtDuration(lastPredict.duration_ms)}</div>
          )}
        </div>

        {/* SMS */}
        <div className={`pipeline-card ${loop.sms_status === "ok" ? "card-ok" : "card-warn"}`}>
          <div className="pipeline-card-title">
            <StatusDot status={loop.sms_status === "ok" ? "ok" : loop.sms_status === "degraded" ? "error" : "unknown"} />
            SMS
          </div>
          <div className="pipeline-card-detail">{loop.sms_status}</div>
          {lastDailyLoop?.at && (
            <div className="pipeline-card-meta">{fmtAt(lastDailyLoop.at)}</div>
          )}
        </div>

        {/* Tomorrow's plan */}
        <div className={`pipeline-card ${loop.tomorrow ? "card-ok" : "card-warn"}`}>
          <div className="pipeline-card-title">
            <StatusDot status={loop.tomorrow ? "ok" : "unknown"} />
            Tomorrow Plan
          </div>
          {loop.tomorrow ? (
            <>
              <div className="pipeline-card-detail">
                {loop.tomorrow.predicted_drinks} drinks · {loop.tomorrow.staffing_mode}
              </div>
              <div className="pipeline-card-meta">
                {loop.tomorrow.confidence != null
                  ? `${Math.round(loop.tomorrow.confidence * 100)}% confidence`
                  : ""}
                {" · "}{fmtAt(loop.tomorrow.generated_at)}
              </div>
            </>
          ) : (
            <div className="pipeline-card-detail">Not generated yet</div>
          )}
        </div>

        {/* 7d accuracy */}
        <div className={`pipeline-card ${loop.accuracy_7d.alert ? "card-warn" : "card-ok"}`}>
          <div className="pipeline-card-title">
            <StatusDot status={loop.accuracy_7d.alert ? "yellow" : "ok"} />
            7-Day Accuracy
          </div>
          <div className="pipeline-card-detail">
            {loop.accuracy_7d.avg != null ? `${loop.accuracy_7d.avg.toFixed(1)}%` : "—"}
          </div>
          <div className="pipeline-card-meta">
            {loop.accuracy_7d.days_measured} days measured
            {loop.accuracy_7d.trend ? ` · ${loop.accuracy_7d.trend}` : ""}
          </div>
          {loop.accuracy_7d.alert_reason && (
            <div className="pipeline-card-error">{loop.accuracy_7d.alert_reason}</div>
          )}
        </div>
      </div>

      {/* Per-job health table */}
      {health && health.components.length > 0 && (
        <section className="pipeline-section">
          <h2>24h Job Health</h2>
          <table className="health-table">
            <thead>
              <tr>
                <th></th>
                <th>Job</th>
                <th>Runs</th>
                <th>OK</th>
                <th>Errors</th>
                <th>Success Rate</th>
                <th>Last Run</th>
              </tr>
            </thead>
            <tbody>
              {health.components.map((c) => (
                <tr key={c.job_name} className={c.error_runs > 0 ? "row-warn" : ""}>
                  <td><StatusDot status={c.error_runs > 0 ? (c.error_runs >= 3 ? "error" : "yellow") : "ok"} /></td>
                  <td className="job-name">{c.job_name}</td>
                  <td>{c.total_runs}</td>
                  <td className="cell-ok">{c.ok_runs}</td>
                  <td className={c.error_runs > 0 ? "cell-err" : ""}>{c.error_runs || "—"}</td>
                  <td>{c.success_rate != null ? `${Math.round(c.success_rate * 100)}%` : "—"}</td>
                  <td className="cell-time">{fmtAt(c.last_started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Recent runs log */}
      {runs && runs.runs.length > 0 && (
        <section className="pipeline-section">
          <h2>Recent Runs</h2>
          <table className="runs-table">
            <thead>
              <tr>
                <th></th>
                <th>Job</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.runs.map((r) => (
                <RunRow key={r.run_id} run={r} />
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
