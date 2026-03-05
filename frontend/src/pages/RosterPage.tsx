import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { StaffingVarianceResponse, StaffingInterval } from "../types/api";

function statusClass(status: StaffingInterval["status"]): string {
  switch (status) {
    case "understaffed": return "status-under";
    case "overstaffed": return "status-over";
    case "no_staff": return "status-nostaff";
    default: return "status-ok";
  }
}

function statusLabel(status: StaffingInterval["status"]): string {
  switch (status) {
    case "understaffed": return "Under";
    case "overstaffed": return "Over";
    case "no_staff": return "No Staff";
    case "balanced": return "OK";
    default: return "—";
  }
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  const h = d.getHours();
  const m = d.getMinutes();
  const ampm = h >= 12 ? "pm" : "am";
  const h12 = h % 12 || 12;
  return m === 0 ? `${h12}${ampm}` : `${h12}:${String(m).padStart(2, "0")}${ampm}`;
}

export function RosterPage() {
  const { user } = useAuth();
  const [data, setData] = useState<StaffingVarianceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    apiFetch<StaffingVarianceResponse>(
      `/api/sites/${user.site_id}/analysis/staffing-variance`
    )
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  if (loading) return <div className="loading">Loading roster...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="empty">No roster data.</div>;

  const { summary, intervals } = data;

  // Only show intervals with workload or staff
  const visible = intervals.filter(
    (i) => i.workload_units > 0 || i.staff_on > 0
  );

  const peak = summary.peak_workload_units;

  return (
    <div className="roster-page">
      <div className="page-header">
        <h1 className="page-title">Staffing Variance</h1>
        <span className="page-subtitle">{summary.date}</span>
      </div>

      <div className="roster-summary">
        <div className="stat">
          <span className="stat-value stat-bad">{summary.understaffed_intervals}</span>
          <span className="stat-label">Understaffed</span>
        </div>
        <div className="stat">
          <span className="stat-value stat-warn">{summary.overstaffed_intervals}</span>
          <span className="stat-label">Overstaffed</span>
        </div>
        <div className="stat">
          <span className="stat-value">{summary.balanced_intervals}</span>
          <span className="stat-label">Balanced</span>
        </div>
        <div className="stat">
          <span className="stat-value">{summary.peak_workload_units.toFixed(1)}</span>
          <span className="stat-label">Peak WU</span>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="empty">No workload or roster data for today.</div>
      ) : (
        <div className="roster-timeline">
          {visible.map((interval, i) => {
            const barPct = peak > 0 ? Math.min(100, (interval.workload_units / peak) * 100) : 0;
            return (
              <div key={i} className={`roster-row ${statusClass(interval.status)}`}>
                <span className="roster-time">{fmtTime(interval.interval_start)}</span>
                <div className="roster-bar-wrap">
                  <div className="roster-bar" style={{ width: `${barPct}%` }} />
                </div>
                <span className="roster-wu">{interval.workload_units.toFixed(1)} WU</span>
                <span className="roster-staff">{interval.staff_on} staff</span>
                <span className={`roster-status ${statusClass(interval.status)}`}>
                  {statusLabel(interval.status)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
