"""
Clubhouse Autopilot - Deputy API Client
Lightweight integration for fetching roster/shift data from Deputy.

Follows the same patterns as data/ingestion.py (SquareIngestion).
Designed to fail quietly when credentials are not configured.
"""

import logging
from datetime import date, datetime

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
            raise DeputyError(
                "Deputy not configured. Set DEPUTY_BASE_URL and DEPUTY_ACCESS_TOKEN."
            )

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

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
        Returns normalized roster records ready for storage.
        """
        # Deputy dates are epoch timestamps at midnight
        start_epoch = int(datetime.combine(start_date, datetime.min.time()).timestamp())
        end_epoch = int(datetime.combine(end_date, datetime.max.time()).timestamp())

        payload = {
            "search": {
                "s1": {"field": "Date", "type": "ge", "data": start_epoch},
                "s2": {"field": "Date", "type": "le", "data": end_epoch},
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
        # Deputy stores dates as epoch timestamps
        shift_date = date.fromtimestamp(raw["Date"]) if raw.get("Date") else None
        start_ts = datetime.fromtimestamp(raw["StartTime"]) if raw.get("StartTime") else None
        end_ts = datetime.fromtimestamp(raw["EndTime"]) if raw.get("EndTime") else None

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
