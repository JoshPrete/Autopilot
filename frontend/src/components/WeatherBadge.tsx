import type { Weather } from "../types/api";

export function WeatherBadge({ weather }: { weather: Weather }) {
  if (!weather.description) return null;

  return (
    <div className="weather-badge">
      <span className="weather-temp">
        {weather.temp_c != null ? `${weather.temp_c}°C` : "--"}
      </span>
      <span className="weather-desc">{weather.description}</span>
      {weather.rain_probability != null && weather.rain_probability > 0 && (
        <span className="weather-rain">{weather.rain_probability}% rain</span>
      )}
    </div>
  );
}
