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

    # Scheduler control — set true when an external orchestrator owns cron runs
    AUTOPILOT_DISABLE_SCHEDULER: bool = _env_bool("AUTOPILOT_DISABLE_SCHEDULER", False)

    def validate_startup(self) -> list[str]:
        """
        Check env configuration at startup. Returns a list of warning strings.

        Logs warnings prominently so operators discover misconfiguration at boot
        rather than when a scheduled job silently fails at 5pm.
        """
        warnings: list[str] = []

        if not self.SQUARE_ACCESS_TOKEN:
            warnings.append("SQUARE_ACCESS_TOKEN not set — ingest step will fail")
        if not self.SQUARE_LOCATION_ID:
            warnings.append("SQUARE_LOCATION_ID not set — scheduled jobs will be skipped")

        twilio_fields = [self.TWILIO_ACCOUNT_SID, self.TWILIO_AUTH_TOKEN, self.TWILIO_PHONE_NUMBER]
        if not all(twilio_fields):
            missing = [
                n
                for n, v in [
                    ("TWILIO_ACCOUNT_SID", self.TWILIO_ACCOUNT_SID),
                    ("TWILIO_AUTH_TOKEN", self.TWILIO_AUTH_TOKEN),
                    ("TWILIO_PHONE_NUMBER", self.TWILIO_PHONE_NUMBER),
                ]
                if not v
            ]
            warnings.append(
                f"Twilio credentials incomplete ({', '.join(missing)}) — SMS delivery disabled"
            )

        if not self.ANTHROPIC_API_KEY:
            warnings.append(
                "ANTHROPIC_API_KEY not set — intelligence LLM synthesis and chat disabled"
            )
        if not self.DEPUTY_ACCESS_TOKEN:
            warnings.append("DEPUTY_ACCESS_TOKEN not set — Deputy roster sync disabled")
        if not self.XERO_CLIENT_ID or not self.XERO_CLIENT_SECRET:
            warnings.append("Xero credentials not set — Xero sync disabled (fail-quiet)")

        if not os.environ.get("JWT_SECRET"):
            warnings.append(
                "JWT_SECRET not set — secret auto-generated at startup, all sessions "
                "will be invalidated on every restart. Set JWT_SECRET in .env for "
                "persistent authentication."
            )

        if (self.XERO_CLIENT_ID or self.XERO_CLIENT_SECRET) and not self.AUTOPILOT_TOKEN_ENC_KEY:
            warnings.append(
                "AUTOPILOT_TOKEN_ENC_KEY not set — Xero OAuth tokens cannot be persisted "
                "and will be lost on restart"
            )

        return warnings


settings = Settings()
