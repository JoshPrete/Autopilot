"""
Clubhouse Autopilot - Deputy API Client
Lightweight integration for fetching roster/shift data from Deputy.

Follows the same patterns as data/ingestion.py (SquareIngestion).
Designed to fail quietly when credentials are not configured.
"""

import logging
from datetime import date, datetime, timedelta

import requests

from config.settings import settings

logger = logging.getLogger("autopilot.deputy")


class DeputyError(Exception):
    """Raised when Deputy API calls fail."""

    pass


class DeputyClient:
    """
    Client for the Deputy workforce management API.

    Requires:
        - DEPUTY_BASE_URL: e.g. https://{install}.{geo}.deputy.com
        - DEPUTY_ACCESS_TOKEN: OAuth2 bearer token

    Usage:
        client = DeputyClient()
        rosters = client.fetch_rosters(date(2026, 2, 10), date(2026, 2, 14))
        employees = client.fetch_employees()
    """

    def __init__(self, base_url: str = None, access_token: str = None):
        self.base_url = (base_url or settings.DEPUTY_BASE_URL).rstrip("/")
        self.access_token = access_token or settings.DEPUTY_ACCESS_TOKEN

        if not self.base_url or not self.access_token:
            raise DeputyError("Deputy not configured. Set DEPUTY_BASE_URL and DEPUTY_ACCESS_TOKEN.")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, endpoint: str, **kwargs) -> dict | list:
        """Make an authenticated request to the Deputy API."""
        url = f"{self.base_url}/api/v1/{endpoint.lstrip('/')}"
        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise DeputyError(f"Deputy API error: {e}") from e

    def fetch_rosters(self, start_date: date, end_date: date) -> list[dict]:
        """
        Fetch roster/shift records for a date range.

        Uses POST /api/v1/resource/Roster/QUERY with date filters.
        Deputy Date field is ISO-8601 string, filtered via string comparison.
        Returns normalized roster records ready for storage.
        """
        # Use [start_date, end_date + 1 day) so all timestamps on end_date are included.
        payload = {
            "search": {
                "s1": {"field": "Date", "type": "ge", "data": str(start_date)},
                "s2": {"field": "Date", "type": "lt", "data": str(end_date + timedelta(days=1))},
            }
        }

        raw_rosters = self._request("POST", "resource/Roster/QUERY", json=payload)
        if not isinstance(raw_rosters, list):
            logger.warning("Unexpected roster response type: %s", type(raw_rosters))
            return []

        rosters = []
        for r in raw_rosters:
            try:
                rosters.append(self._normalize_roster(r))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Skipping malformed roster record %s: %s", r.get("Id"), e)
                continue

        logger.info("Fetched %d rosters for %s to %s", len(rosters), start_date, end_date)
        return rosters

    def _normalize_roster(self, raw: dict) -> dict:
        """Convert a raw Deputy roster record to our storage format."""
        # Date is ISO string "2026-02-12T00:00:00+10:00"
        shift_date = None
        if raw.get("Date"):
            shift_date = datetime.fromisoformat(raw["Date"]).date()

        # StartTimeLocalized / EndTimeLocalized are ISO strings with timezone
        start_ts = None
        if raw.get("StartTimeLocalized"):
            start_ts = datetime.fromisoformat(raw["StartTimeLocalized"])
        elif raw.get("StartTime"):
            start_ts = datetime.fromtimestamp(raw["StartTime"])

        end_ts = None
        if raw.get("EndTimeLocalized"):
            end_ts = datetime.fromisoformat(raw["EndTimeLocalized"])
        elif raw.get("EndTime"):
            end_ts = datetime.fromtimestamp(raw["EndTime"])

        return {
            "deputy_id": raw["Id"],
            "shift_date": shift_date,
            "start_time": start_ts,
            "end_time": end_ts,
            "employee_id": raw.get("Employee"),
            "total_hours": raw.get("TotalTime"),
            "cost_dollars": raw.get("Cost"),
            "is_published": bool(raw.get("Published", True)),
            "is_open": bool(raw.get("Open", False)),
        }

    def fetch_employees(self) -> dict[int, str]:
        """
        Fetch employee ID -> name mapping.

        Uses POST /api/v1/resource/Employee/QUERY.
        Returns {employee_id: "First Last"} dict.
        """
        payload = {
            "search": {
                "s1": {"field": "Active", "type": "eq", "data": 1},
            }
        }

        raw_employees = self._request("POST", "resource/Employee/QUERY", json=payload)
        if not isinstance(raw_employees, list):
            logger.warning("Unexpected employee response type: %s", type(raw_employees))
            return {}

        employees = {}
        for emp in raw_employees:
            emp_id = emp.get("Id")
            first = emp.get("FirstName", "")
            last = emp.get("LastName", "")
            if emp_id is not None:
                employees[int(emp_id)] = f"{first} {last}".strip()

        logger.info("Fetched %d employees", len(employees))
        return employees


def is_deputy_configured() -> bool:
    """Check if Deputy credentials are set (used for fail-quiet checks)."""
    return bool(settings.DEPUTY_BASE_URL and settings.DEPUTY_ACCESS_TOKEN)
