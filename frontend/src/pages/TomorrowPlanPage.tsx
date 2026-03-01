import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { RushWindowCard } from "../components/RushWindowCard";
import { WeatherBadge } from "../components/WeatherBadge";
import type { TomorrowPlanResponse } from "../types/api";

export function TomorrowPlanPage() {
  const { user } = useAuth();
  const [plan, setPlan] = useState<TomorrowPlanResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) return;
    apiFetch<TomorrowPlanResponse>(
      `/api/sites/${user.site_id}/tomorrow-plan/json`
    )
      .then(setPlan)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [user]);

  if (loading) return <div className="loading">Loading tomorrow plan...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!plan) return <div className="empty">No plan available.</div>;

  const { meta, forecast, weather, rush_windows, hourly } = plan;

  return (
    <div className="plan-page">
      <div className="plan-header">
        <div>
          <h1>
            {meta.day_name} {meta.forecast_date}
          </h1>
          <p className="plan-subtitle">
            {meta.staffing_mode} &middot; {meta.staff_scheduled} staff
            {meta.confidence_label && (
              <span className={`confidence confidence-${meta.confidence_label}`}>
                {" "}
                &middot; {meta.confidence_label} confidence
                {meta.confidence !== null && ` (${Math.round(meta.confidence * 100)}%)`}
              </span>
            )}
          </p>
        </div>
        <WeatherBadge weather={weather} />
      </div>

      <div className="forecast-summary">
        <div className="stat">
          <span className="stat-value">{forecast.total_predicted_drinks}</span>
          <span className="stat-label">Predicted Drinks</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {forecast.total_predicted_workload?.toFixed(0)}
          </span>
          <span className="stat-label">Workload Units</span>
        </div>
        <div className="stat">
          <span className="stat-value">{rush_windows.length}</span>
          <span className="stat-label">Rush Windows</span>
        </div>
        {forecast.event_multiplier !== 1.0 && (
          <div className="stat">
            <span className="stat-value">
              {forecast.event_multiplier.toFixed(1)}x
            </span>
            <span className="stat-label">Event Multiplier</span>
          </div>
        )}
      </div>

      {rush_windows.length > 0 && (
        <section className="rush-section">
          <h2>Rush Windows</h2>
          <div className="rush-grid">
            {rush_windows.map((rw) => (
              <RushWindowCard key={rw.window_number} rush={rw} />
            ))}
          </div>
        </section>
      )}

      {hourly.length > 0 && (
        <section className="hourly-section">
          <h2>Hourly Breakdown</h2>
          <div className="hourly-grid">
            {hourly.map((h) => (
              <div
                key={h.hour}
                className={`hourly-bar ${h.is_rush ? "hourly-rush" : ""}`}
              >
                <span className="hourly-label">{h.hour_label}</span>
                <div
                  className="hourly-fill"
                  style={{
                    height: `${Math.min(100, (h.predicted_workload / 120) * 100)}%`,
                  }}
                />
                <span className="hourly-value">
                  {h.predicted_workload?.toFixed(0)}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <p className="plan-footer">
        Generated: {meta.generated_at} &middot; Prediction: {meta.prediction_id?.slice(0, 8)}
      </p>
    </div>
  );
}
