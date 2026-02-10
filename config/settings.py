import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Square POS
    SQUARE_ACCESS_TOKEN: str = os.environ.get("SQUARE_ACCESS_TOKEN", "")
    SQUARE_LOCATION_ID: str = os.environ.get("SQUARE_LOCATION_ID", "")
    SQUARE_ENVIRONMENT: str = os.environ.get("SQUARE_ENVIRONMENT", "production")
    SQUARE_TERMINAL_DEVICE_ID: str = os.environ.get(
        "SQUARE_TERMINAL_DEVICE_ID", ""
    )
    SQUARE_WEBHOOK_SIGNATURE_KEY: str = os.environ.get(
        "SQUARE_WEBHOOK_SIGNATURE_KEY", ""
    )
    SQUARE_WEBHOOK_NOTIFICATION_URL: str = os.environ.get(
        "SQUARE_WEBHOOK_NOTIFICATION_URL", ""
    )
    SQUARE_SANDBOX_TEST_SOURCE_ID: str = os.environ.get(
        "SQUARE_SANDBOX_TEST_SOURCE_ID", "cnon:card-nonce-ok"
    )

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
    SITE_TIMEZONE: str = os.environ.get("SITE_TIMEZONE", "Australia/Brisbane")
    MANAGER_PHONE: str = os.environ.get("MANAGER_PHONE", "")

    # Autopilot Tuning
    RUSH_THRESHOLD_MULTIPLIER: float = float(
        os.environ.get("RUSH_THRESHOLD_MULTIPLIER", "1.3")
    )
    WALLY_TRIGGER_MILK_DRINKS: int = int(
        os.environ.get("WALLY_TRIGGER_MILK_DRINKS", "3")
    )
    RECENT_PATTERN_WEEKS: int = int(os.environ.get("RECENT_PATTERN_WEEKS", "6"))
    YOY_YEARS_BACK: int = int(os.environ.get("YOY_YEARS_BACK", "2"))

    # Prediction Weights
    WEIGHT_RECENT: float = 0.60
    WEIGHT_YOY: float = 0.25
    WEIGHT_DOW: float = 0.15

    # Workload Interval
    WORKLOAD_INTERVAL_MINUTES: int = 15


settings = Settings()
