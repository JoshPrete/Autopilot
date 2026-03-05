import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  AccuracyDashboard,
  AccuracyDailyPoint,
  AccuracyByDow,
} from "../types/api";

// ── SVG line chart ──────────────────────────────────────────

const W = 600;
const H = 140;
const PAD = { top: 12, right: 16, bottom: 28, left: 36 };

function scaleX(i: number, n: number): number {
  return PAD.left + (i / Math.max(n - 1, 1)) * (W - PAD.left - PAD.right);
}

function scaleY(v: number): number {
  // accuracy range 0-100 → H
  return PAD.top + (1 - v / 100) * (H - PAD.top - PAD.bottom);
}

function toPath(points: { x: number; y: number }[]): string {
  if (!points.length) return "";
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}

function TrendChart({ data }: { data: AccuracyDailyPoint[] }) {
  if (!data.length) return null;
  const n = data.length;

  const accPoints = data.map((d, i) => ({ x: scaleX(i, n), y: scaleY(d.accuracy) }));
  const rollPoints = data.map((d, i) => ({ x: scaleX(i, n), y: scaleY(d.rolling_7d_avg) }));

  // x-axis labels: show ~5 evenly spaced
  const labelIndices = Array.from({ length: Math.min(5, n) }, (_, i) =>
    Math.round((i * (n - 1)) / (Math.min(5, n) - 1 || 1))
  );

  const gridLines = [75, 80, 85, 90, 95, 100];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="trend-chart" aria-label="Accuracy trend">
      {/* grid */}
      {gridLines.map((v) => {
        const y = scaleY(v);
        return (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="#e0e0e0" strokeWidth="1" />
            <text x={PAD.left - 4} y={y + 4} textAnchor="end" fontSize="9" fill="#aaa">{v}</text>
          </g>
        );
      })}

      {/* 75% threshold line */}
      <line
        x1={PAD.left} x2={W - PAD.right}
        y1={scaleY(75)} y2={scaleY(75)}
        stroke="#ef9a9a" strokeWidth="1" strokeDasharray="4 3"
      />

      {/* rolling 7d avg */}
      <path d={toPath(rollPoints)} fill="none" stroke="#90a4ae" strokeWidth="2" strokeDasharray="4 3" />

      {/* daily accuracy */}
      <path d={toPath(accPoints)} fill="none" stroke="#1a237e" strokeWidth="2" />

      {/* dots for daily */}
      {accPoints.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="2.5" fill={data[i].accuracy < 75 ? "#e53935" : "#1a237e"} />
      ))}

      {/* x-axis labels */}
      {labelIndices.map((idx) => (
        <text key={idx} x={scaleX(idx, n)} y={H - 4} textAnchor="middle" fontSize="9" fill="#999">
          {data[idx].date.slice(5)}
        </text>
      ))}
    </svg>
  );
}

// ── DOW bars ────────────────────────────────────────────────

function DowBars({ data }: { data: AccuracyByDow[] }) {
  return (
    <div className="dow-bars">
      {data.map((d) => {
        const pct = d.avg_accuracy;
        const cls = pct < 75 ? "bar-bad" : pct < 85 ? "bar-mid" : "bar-good";
        const bias = d.avg_signed_error;
        return (
          <div key={d.dow} className="dow-row">
            <span className="dow-name">{d.day_name}</span>
            <div className="dow-bar-wrap">
              <div className={`dow-bar ${cls}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="dow-pct">{pct.toFixed(1)}%</span>
            {bias != null && (
              <span className={`dow-bias ${bias > 0 ? "bias-over" : "bias-under"}`}>
                {bias > 0 ? `+${bias}` : bias} drinks
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────

export function AccuracyPage() {
  const { user } = useAuth();
  const [data, setData] = useState<AccuracyDashboard | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    setError("");
    apiFetch<AccuracyDashboard>(
      `/api/sites/${user.site_id}/analysis/accuracy-dashboard?days_back=${days}`
    )
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user, days]);

  if (loading) return <div className="loading">Loading accuracy data...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="empty">No accuracy data.</div>;

  const { summary } = data;
  const trendCls =
    summary.trend === "improving" ? "trend-up" : summary.trend === "declining" ? "trend-down" : "trend-stable";
  const trendLabel =
    summary.trend === "improving" ? "↑ Improving" : summary.trend === "declining" ? "↓ Declining" : "→ Stable";

  return (
    <div className="accuracy-page">
      <div className="page-header">
        <h1 className="page-title">Accuracy Dashboard</h1>
        <div className="days-toggle">
          {[14, 30, 60].map((d) => (
            <button
              key={d}
              className={`days-btn${days === d ? " active" : ""}`}
              onClick={() => setDays(d)}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {data.alert && data.alert_reason && (
        <div className="error-banner acc-alert">{data.alert_reason}</div>
      )}

      {/* Summary stats */}
      <div className="acc-summary">
        <div className="stat">
          <span className="stat-value">{summary.avg_accuracy != null ? `${summary.avg_accuracy.toFixed(1)}%` : "—"}</span>
          <span className="stat-label">Avg Accuracy</span>
        </div>
        <div className="stat">
          <span className={`stat-value acc-trend ${trendCls}`}>{trendLabel}</span>
          <span className="stat-label">Trend ({data.days_measured} days)</span>
        </div>
        <div className="stat">
          <span className="stat-value">{summary.avg_abs_error != null ? `±${summary.avg_abs_error}` : "—"}</span>
          <span className="stat-label">Avg Error (drinks)</span>
        </div>
        <div className="stat">
          <span className={`stat-value ${(summary.avg_signed_error ?? 0) > 0 ? "bias-over" : "bias-under"}`}>
            {summary.avg_signed_error != null
              ? `${summary.avg_signed_error > 0 ? "+" : ""}${summary.avg_signed_error}`
              : "—"}
          </span>
          <span className="stat-label">Systematic Bias</span>
        </div>
        {summary.calibrated != null && (
          <div className="stat">
            <span className={`stat-value ${summary.calibrated ? "cal-ok" : "cal-warn"}`}>
              {summary.calibrated ? "Yes" : "No"}
            </span>
            <span className="stat-label">Calibrated</span>
          </div>
        )}
        {summary.worst_day && (
          <div className="stat">
            <span className="stat-value stat-sm">{summary.worst_day}</span>
            <span className="stat-label">Hardest Day</span>
          </div>
        )}
        {summary.best_day && (
          <div className="stat">
            <span className="stat-value stat-sm">{summary.best_day}</span>
            <span className="stat-label">Best Day</span>
          </div>
        )}
      </div>

      {/* Daily trend chart */}
      {data.daily_trend.length > 0 && (
        <section className="acc-section">
          <div className="acc-section-header">
            <h2>Daily Accuracy</h2>
            <div className="chart-legend">
              <span className="legend-item legend-acc">Daily</span>
              <span className="legend-item legend-roll">7-day avg</span>
              <span className="legend-item legend-thresh">75% threshold</span>
            </div>
          </div>
          <div className="chart-wrap">
            <TrendChart data={data.daily_trend} />
          </div>
        </section>
      )}

      {/* DOW breakdown */}
      {data.by_dow.length > 0 && (
        <section className="acc-section">
          <h2>By Day of Week</h2>
          <DowBars data={data.by_dow} />
        </section>
      )}

      {/* Confidence calibration + staffing mode side by side */}
      <div className="acc-two-col">
        {data.by_confidence.length > 0 && (
          <section className="acc-section acc-section-half">
            <h2>By Confidence Band</h2>
            <table className="acc-table">
              <thead>
                <tr><th>Band</th><th>Confidence Range</th><th>Avg Accuracy</th><th>Days</th></tr>
              </thead>
              <tbody>
                {data.by_confidence.map((c) => (
                  <tr key={c.band}>
                    <td><span className={`conf-badge conf-${c.band}`}>{c.band}</span></td>
                    <td className="cell-muted">{c.label}</td>
                    <td className={c.avg_accuracy < 75 ? "cell-err" : "cell-ok"}>{c.avg_accuracy.toFixed(1)}%</td>
                    <td className="cell-muted">{c.sample_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {data.by_staffing_mode.length > 0 && (
          <section className="acc-section acc-section-half">
            <h2>By Staffing Mode</h2>
            <table className="acc-table">
              <thead>
                <tr><th>Mode</th><th>Avg Accuracy</th><th>Days</th></tr>
              </thead>
              <tbody>
                {data.by_staffing_mode.map((m) => (
                  <tr key={m.mode}>
                    <td className="cell-mode">{m.mode}</td>
                    <td className={m.avg_accuracy < 75 ? "cell-err" : "cell-ok"}>{m.avg_accuracy.toFixed(1)}%</td>
                    <td className="cell-muted">{m.sample_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </div>

      {/* Predicted vs actual table */}
      {data.predicted_vs_actual.length > 0 && (
        <section className="acc-section">
          <h2>Predicted vs Actual</h2>
          <table className="acc-table pva-table">
            <thead>
              <tr><th>Date</th><th>Predicted</th><th>Actual</th><th>Error</th><th>Error %</th></tr>
            </thead>
            <tbody>
              {data.predicted_vs_actual.slice().reverse().map((r) => (
                <tr key={r.date} className={r.error_pct > 25 ? "row-warn" : ""}>
                  <td>{r.date}</td>
                  <td>{r.predicted}</td>
                  <td>{r.actual}</td>
                  <td>{r.predicted > r.actual ? `+${r.error}` : `-${r.error}`}</td>
                  <td className={r.error_pct > 25 ? "cell-err" : r.error_pct > 15 ? "cell-warn" : ""}>
                    {r.error_pct.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
