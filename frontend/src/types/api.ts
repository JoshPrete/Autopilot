export interface TomorrowPlanMeta {
  prediction_id: string;
  forecast_date: string;
  day_name: string;
  generated_at: string;
  staffing_mode: string;
  confidence: number | null;
  confidence_label: string | null;
  staff_scheduled: number;
}

export interface TomorrowPlanForecast {
  total_predicted_drinks: number;
  total_predicted_workload: number;
  event_multiplier: number;
}

export interface Weather {
  temp_c: number | null;
  description: string | null;
  rain_probability: number | null;
  humidity: number | null;
}

export interface WallySplit {
  full_cream?: number;
  oat?: number;
  soy?: number;
}

export interface RushWindow {
  window_number: number;
  start: string;
  end: string;
  duration_minutes: number;
  predicted_drinks: number;
  wally_start_time: string;
  wally_volume_litres: number;
  wally_split: WallySplit;
  switch_3p_time: string;
  alert_time: string;
  pre_rush_checklist: string[];
}

export interface HourlyEntry {
  hour: number;
  hour_label: string;
  predicted_workload: number;
  is_rush: boolean;
}

export interface TomorrowPlanResponse {
  meta: TomorrowPlanMeta;
  forecast: TomorrowPlanForecast;
  weather: Weather;
  rush_windows: RushWindow[];
  hourly: HourlyEntry[];
}

export interface AuthUser {
  contact_id: string;
  site_id: string;
  role: string;
  name: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  name: string;
  role: string;
}

// Scorecard
export interface ScorecardKPIs {
  revenue_cents: number | null;
  labor_cost_cents: number | null;
  cogs_cents: number | null;
  gross_profit_cents: number | null;
  net_profit_cents: number | null;
  labor_pct: number | null;
  revenue_per_labor_hour: number | null;
  days_with_data: number;
}

export interface ScorecardTrendWindow {
  revenue_cents: number | null;
  labor_cost_cents: number | null;
  net_profit_cents: number | null;
  labor_pct: number | null;
}

export interface ScorecardTrend {
  current_window: ScorecardTrendWindow;
  previous_window: ScorecardTrendWindow;
  deltas: Record<string, number | null>;
  directions: Record<string, string>;
}

export interface ScorecardActions {
  recommendations_generated: number;
  recommendations_adopted: number;
  adoption_rate: number | null;
  realized_actions: number;
  avg_realized_weekly_profit_delta_cents: number | null;
  total_realized_weekly_profit_delta_cents: number | null;
  top_proven_action_types: string[];
}

export interface ScorecardResponse {
  site_id: string;
  window: { days: number; compare_days: number; start_date: string; end_date: string };
  headline: string;
  kpis: ScorecardKPIs;
  trend: ScorecardTrend;
  actions: ScorecardActions;
}

// Next Actions / Insights
export interface ActionMemory {
  sample_count: number;
  avg_realized_weekly_profit_delta_cents: number | null;
}

export interface NextAction {
  action_key: string;
  action_type: string;
  title: string;
  reason: string;
  confidence: number;
  expected_weekly_profit_uplift_cents: number | null;
  proven_weekly_impact_cents: number | null;
  memory: ActionMemory | null;
  optimization_phase: string | null;
}

export interface NextActionsResponse {
  site_id: string;
  target_date: string;
  actions: NextAction[];
  optimization_phase: string | null;
  phase_reason: string | null;
  data_health: Record<string, unknown>;
}

// Accuracy Dashboard
export interface AccuracySummary {
  avg_accuracy: number | null;
  trend: "improving" | "declining" | "stable";
  avg_abs_error: number | null;
  avg_signed_error: number | null;
  worst_day: string | null;
  best_day: string | null;
  calibrated: boolean | null;
}

export interface AccuracyDailyPoint {
  date: string;
  accuracy: number;
  rolling_7d_avg: number;
}

export interface AccuracyPredictedVsActual {
  date: string;
  predicted: number;
  actual: number;
  error: number;
  error_pct: number;
}

export interface AccuracyByDow {
  dow: number;
  day_name: string;
  avg_accuracy: number;
  sample_count: number;
  avg_signed_error: number | null;
}

export interface AccuracyByConfidence {
  band: string;
  label: string;
  avg_accuracy: number;
  sample_count: number;
}

export interface AccuracyByStaffingMode {
  mode: string;
  avg_accuracy: number;
  sample_count: number;
}

export interface AccuracyDashboard {
  days_back: number;
  days_measured: number;
  summary: AccuracySummary;
  alert: boolean;
  alert_reason: string | null;
  daily_trend: AccuracyDailyPoint[];
  predicted_vs_actual: AccuracyPredictedVsActual[];
  by_dow: AccuracyByDow[];
  by_confidence: AccuracyByConfidence[];
  by_staffing_mode: AccuracyByStaffingMode[];
}

// Pipeline Status
export interface PipelineLastRun {
  status: string;
  at: string | null;
  duration_ms: number | null;
  error?: string | null;
}

export interface PipelineTomorrow {
  prediction_id: string;
  predicted_drinks: number | null;
  staffing_mode: string | null;
  confidence: number | null;
  generated_at: string | null;
}

export interface DailyLoopStatus {
  site_id: string;
  checked_at: string;
  readiness: "green" | "yellow" | "red";
  last_runs: Record<string, PipelineLastRun>;
  sms_status: "ok" | "degraded" | "no_recipients" | "unknown";
  tomorrow: PipelineTomorrow | null;
  accuracy_7d: {
    avg: number | null;
    trend: string | null;
    alert: boolean;
    alert_reason: string | null;
    days_measured: number;
  };
}

export interface PipelineHealthComponent {
  job_name: string;
  total_runs: number;
  ok_runs: number;
  error_runs: number;
  skipped_runs: number;
  success_rate: number | null;
  last_started_at: string | null;
}

export interface PipelineHealth {
  window_hours: number;
  status: "green" | "yellow" | "red" | "unknown";
  overall_success_rate: number | null;
  total_runs: number;
  ok_runs: number;
  error_runs: number;
  skipped_runs: number;
  components: PipelineHealthComponent[];
}

export interface PipelineRun {
  run_id: string;
  job_name: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_text: string | null;
}

export interface PipelineRunsResponse {
  site_id: string;
  runs: PipelineRun[];
}

// Staffing Variance
export interface StaffingInterval {
  interval_start: string;
  workload_units: number;
  items_count: number;
  staff_on: number;
  expected_staff: number;
  workload_per_staff: number | null;
  status: "understaffed" | "overstaffed" | "balanced" | "no_staff" | "no_workload";
  severity: "high" | "medium" | "low";
}

export interface StaffingVarianceSummary {
  date: string;
  interval_count: number;
  understaffed_intervals: number;
  overstaffed_intervals: number;
  balanced_intervals: number;
  no_staff_intervals: number;
  peak_workload_units: number;
}

export interface StaffingVarianceResponse {
  site_id: string;
  target_date: string;
  summary: StaffingVarianceSummary;
  intervals: StaffingInterval[];
}
