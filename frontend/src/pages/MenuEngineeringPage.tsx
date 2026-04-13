import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface SaleProfile {
  family: string | null;
  size_label: string | null;
  serve_temperature: string | null;
  service_mode: string | null;
  variant_key: string | null;
}

interface MenuItem {
  item: string;
  score_key: string;
  category: string;
  qty: number;
  avg_price_cents: number;
  cogs_cents: number;
  cogs_source: string;
  cogs_source_label: string;
  cogs_detail?: string | null;
  cogs_components: {
    item_name: string;
    quantity: number;
    unit: string;
    cost_cents: number;
    source: string;
    basis: string;
  }[];
  margin_pct: number;
  total_profit_cents: number;
  quadrant: "star" | "cash_cow" | "question_mark" | "laggard";
  quadrant_label: string;
  sale_profile: SaleProfile;
  recommendation: string;
}

interface QuadrantSummary {
  count: number;
  total_profit_cents: number;
}

interface MenuMatrixResponse {
  window_days: number;
  item_count: number;
  thresholds: { popularity_median: number; margin_median: number };
  items: MenuItem[];
  quadrant_summary: {
    star: QuadrantSummary;
    cash_cow: QuadrantSummary;
    question_mark: QuadrantSummary;
    laggard: QuadrantSummary;
  };
}

function cents(v: number): string {
  if (v >= 100_000) return `$${(v / 100_000).toFixed(1)}k`;
  return `$${(v / 100).toFixed(0)}`;
}

const QUADRANT_COLORS: Record<string, string> = {
  star: "#22c55e",
  cash_cow: "#3b82f6",
  question_mark: "#f59e0b",
  laggard: "#ef4444",
};

const QUADRANT_BG: Record<string, string> = {
  star: "#f0fdf4",
  cash_cow: "#eff6ff",
  question_mark: "#fffbeb",
  laggard: "#fef2f2",
};

function QuadrantCard({
  label,
  quadrant,
  summary,
}: {
  label: string;
  quadrant: string;
  summary: QuadrantSummary;
}) {
  return (
    <div
      className="quadrant-card"
      style={{
        borderLeft: `4px solid ${QUADRANT_COLORS[quadrant]}`,
        background: QUADRANT_BG[quadrant],
      }}
    >
      <div className="quadrant-card-label">{label}</div>
      <div className="quadrant-card-count">{summary.count} items</div>
      <div className="quadrant-card-profit">
        {cents(summary.total_profit_cents)} profit
      </div>
    </div>
  );
}

function ItemRow({ item }: { item: MenuItem }) {
  const [expanded, setExpanded] = useState(false);
  const color = QUADRANT_COLORS[item.quadrant];

  return (
    <>
      <tr
        className="menu-item-row"
        onClick={() => setExpanded((e) => !e)}
        style={{ cursor: "pointer" }}
      >
        <td>
          <span
            className="quadrant-dot"
            style={{ background: color }}
            title={item.quadrant_label}
          />
          {item.item}
        </td>
        <td className="num">{item.qty.toLocaleString()}</td>
        <td className="num">{cents(item.avg_price_cents)}</td>
        <td className="num">{item.margin_pct.toFixed(1)}%</td>
        <td className="num">{cents(item.total_profit_cents)}</td>
        <td>
          <span
            className="quadrant-badge"
            style={{ background: QUADRANT_BG[item.quadrant], color }}
          >
            {item.quadrant_label}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="menu-item-detail">
          <td colSpan={6}>
            <div className="item-detail-inner">
              <p className="item-recommendation">{item.recommendation}</p>
              <div className="item-meta">
                <span>COGS: {cents(item.cogs_cents)} / unit</span>
                <span className={`cost-basis-tag cost-basis-${item.cogs_source}`}>
                  {item.cogs_source_label}
                </span>
                {item.sale_profile.family && (
                  <span>Family: {item.sale_profile.family}</span>
                )}
                {item.sale_profile.size_label && (
                  <span>Size: {item.sale_profile.size_label}</span>
                )}
                {item.sale_profile.serve_temperature && (
                  <span>Temp: {item.sale_profile.serve_temperature}</span>
                )}
              </div>
              {item.cogs_detail && (
                <p className="item-cost-detail">{item.cogs_detail}</p>
              )}
              {!!item.cogs_components?.length && (
                <div className="item-cost-components">
                  {item.cogs_components.slice(0, 4).map((component) => (
                    <span key={`${item.score_key}-${component.item_name}`} className="item-cost-chip">
                      {component.item_name}: {cents(component.cost_cents)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

type FilterQuadrant = "all" | "star" | "cash_cow" | "question_mark" | "laggard";

export function MenuEngineeringPage() {
  const { user } = useAuth();
  const [data, setData] = useState<MenuMatrixResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [days, setDays] = useState(28);
  const [filter, setFilter] = useState<FilterQuadrant>("all");

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    setError("");
    apiFetch<MenuMatrixResponse>(
      `/api/sites/${user.site_id}/analysis/menu-engineering?days=${days}`
    )
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user, days]);

  if (loading) return <div className="loading">Loading menu matrix...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data || data.item_count === 0)
    return <div className="empty">No item data for this window.</div>;

  const { thresholds, quadrant_summary, items } = data;
  const visible =
    filter === "all" ? items : items.filter((i) => i.quadrant === filter);

  return (
    <div className="menu-engineering-page">
      <div className="page-header">
        <h1 className="page-title">Menu Engineering</h1>
        <span className="page-subtitle">
          {data.item_count} items &middot; last {data.window_days} days
        </span>
      </div>

      <div className="window-picker">
        {[14, 28, 56].map((d) => (
          <button
            key={d}
            className={`window-btn${days === d ? " active" : ""}`}
            onClick={() => setDays(d)}
          >
            {d}d
          </button>
        ))}
      </div>

      <div className="quadrant-grid">
        <QuadrantCard
          label="Stars"
          quadrant="star"
          summary={quadrant_summary.star}
        />
        <QuadrantCard
          label="Cash Cows"
          quadrant="cash_cow"
          summary={quadrant_summary.cash_cow}
        />
        <QuadrantCard
          label="Question Marks"
          quadrant="question_mark"
          summary={quadrant_summary.question_mark}
        />
        <QuadrantCard
          label="Laggards"
          quadrant="laggard"
          summary={quadrant_summary.laggard}
        />
      </div>

      <div className="matrix-thresholds">
        Median popularity: <strong>{thresholds.popularity_median.toFixed(0)} units</strong>
        &nbsp;&middot;&nbsp;
        Median margin: <strong>{thresholds.margin_median}%</strong>
      </div>

      <div className="filter-row">
        {(
          [
            ["all", "All"],
            ["star", "Stars"],
            ["cash_cow", "Cash Cows"],
            ["question_mark", "Question Marks"],
            ["laggard", "Laggards"],
          ] as [FilterQuadrant, string][]
        ).map(([q, label]) => (
          <button
            key={q}
            className={`filter-btn${filter === q ? " active" : ""}`}
            onClick={() => setFilter(q)}
          >
            {label}
          </button>
        ))}
      </div>

      <table className="menu-table">
        <thead>
          <tr>
            <th>Item</th>
            <th className="num">Units sold</th>
            <th className="num">Avg price</th>
            <th className="num">Margin</th>
            <th className="num">Total profit</th>
            <th>Quadrant</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((item) => (
            <ItemRow key={item.score_key} item={item} />
          ))}
        </tbody>
      </table>

      {visible.length === 0 && (
        <div className="empty">No items in this quadrant.</div>
      )}
    </div>
  );
}
