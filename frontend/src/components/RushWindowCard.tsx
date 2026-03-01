import type { RushWindow } from "../types/api";

function formatTime(iso: string | null): string {
  if (!iso) return "--";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-AU", { hour: "numeric", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export function RushWindowCard({ rush }: { rush: RushWindow }) {
  return (
    <div className="rush-card">
      <div className="rush-card-header">
        <h3>Rush #{rush.window_number}</h3>
        <span className="rush-time">
          {formatTime(rush.start)} &ndash; {formatTime(rush.end)}
        </span>
      </div>

      <div className="rush-stats">
        <div>
          <strong>{rush.predicted_drinks}</strong> drinks
        </div>
        <div>
          <strong>{rush.duration_minutes}</strong> min
        </div>
      </div>

      <div className="rush-timing">
        <div>
          <span className="timing-label">Wally Start</span>
          <span>{formatTime(rush.wally_start_time)}</span>
        </div>
        <div>
          <span className="timing-label">Switch 3P</span>
          <span>{formatTime(rush.switch_3p_time)}</span>
        </div>
        <div>
          <span className="timing-label">Alert</span>
          <span>{formatTime(rush.alert_time)}</span>
        </div>
      </div>

      {rush.wally_volume_litres > 0 && (
        <div className="rush-wally">
          <strong>Wally:</strong> {rush.wally_volume_litres}L total
          {rush.wally_split && (
            <span className="wally-split">
              {" "}
              (FC: {rush.wally_split.full_cream ?? 0}L, Oat:{" "}
              {rush.wally_split.oat ?? 0}L, Soy: {rush.wally_split.soy ?? 0}L)
            </span>
          )}
        </div>
      )}

      {rush.pre_rush_checklist.length > 0 && (
        <div className="rush-checklist">
          <strong>Pre-Rush Checklist</strong>
          <ul>
            {rush.pre_rush_checklist.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
