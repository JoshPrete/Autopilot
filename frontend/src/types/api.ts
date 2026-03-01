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
