import os
import secrets

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


class Settings:
    # Square POS
    SQUARE_ACCESS_TOKEN: str = os.environ.get("SQUARE_ACCESS_TOKEN", "")
    SQUARE_LOCATION_ID: str = os.environ.get("SQUARE_LOCATION_ID", "")
    SQUARE_ENVIRONMENT: str = os.environ.get("SQUARE_ENVIRONMENT", "production")

    # Twilio SMS
    TWILIO_ACCOUNT_SID: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.environ.get("TWILIO_PHONE_NUMBER", "")

    # Database
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "postgresql://localhost:5432/clubhouse_autopilot"
    )

    # Weather (OpenWeatherMap)
    WEATHER_API_KEY: str = os.environ.get("WEATHER_API_KEY", "")
    WEATHER_LAT: float = float(os.environ.get("WEATHER_LAT", "-27.4005"))  # Nundah
    WEATHER_LON: float = float(os.environ.get("WEATHER_LON", "153.0590"))

    # Site Configuration
    DEFAULT_SITE_ID: str = os.environ.get("DEFAULT_SITE_ID", "")
    SITE_TIMEZONE: str = os.environ.get("SITE_TIMEZONE", "Australia/Brisbane")
    MANAGER_PHONE: str = os.environ.get("MANAGER_PHONE", "")

    # Autopilot Tuning
    RUSH_THRESHOLD_MULTIPLIER: float = float(os.environ.get("RUSH_THRESHOLD_MULTIPLIER", "1.3"))
    WALLY_TRIGGER_MILK_DRINKS: int = int(os.environ.get("WALLY_TRIGGER_MILK_DRINKS", "3"))
    RECENT_PATTERN_WEEKS: int = int(os.environ.get("RECENT_PATTERN_WEEKS", "6"))
    YOY_YEARS_BACK: int = int(os.environ.get("YOY_YEARS_BACK", "2"))

    # Deputy Workforce Management
    DEPUTY_BASE_URL: str = os.environ.get("DEPUTY_BASE_URL", "")
    DEPUTY_ACCESS_TOKEN: str = os.environ.get("DEPUTY_ACCESS_TOKEN", "")

    # Xero Accounting
    XERO_CLIENT_ID: str = os.environ.get("XERO_CLIENT_ID", "")
    XERO_CLIENT_SECRET: str = os.environ.get("XERO_CLIENT_SECRET", "")
    XERO_REDIRECT_URI: str = os.environ.get(
        "XERO_REDIRECT_URI", "http://localhost:8000/api/xero/callback"
    )
    AUTOPILOT_TOKEN_ENC_KEY: str = os.environ.get("AUTOPILOT_TOKEN_ENC_KEY", "")
    ALLOW_AUTO_APPLY_PROPOSED_MAPPINGS: bool = _env_bool(
        "ALLOW_AUTO_APPLY_PROPOSED_MAPPINGS", False
    )
    MIN_CONFIDENCE_AUTO_APPLY: float = float(os.environ.get("MIN_CONFIDENCE_AUTO_APPLY", "0.90"))
    MAX_COST_DELTA_PCT: float = float(os.environ.get("MAX_COST_DELTA_PCT", "40"))
    XERO_MAPPING_PROMPT_VERSION: str = os.environ.get(
        "XERO_MAPPING_PROMPT_VERSION",
        "xero-mapping-v2",
    )

    # JWT Auth (auto-generated if not set)
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "") or secrets.token_urlsafe(32)

    # AI Chat (Claude API)
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

    # Document Uploads
    UPLOAD_DIR: str = os.environ.get("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_SIZE_MB: int = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "10"))

    # Prediction Weights
    WEIGHT_RECENT: float = 0.60
    WEIGHT_YOY: float = 0.25
    WEIGHT_DOW: float = 0.15

    # Workload Interval
    WORKLOAD_INTERVAL_MINUTES: int = 15


settings = Settings()
