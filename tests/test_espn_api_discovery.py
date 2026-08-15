from datetime import datetime, timezone
from unittest.mock import patch, Mock

from collector import discover_tournament_from_espn_api


def test_espn_api_discovers_current_event():
    payload = {
        "events": [{
            "name": "FedEx St. Jude Championship",
            "date": "2026-08-13T04:00Z",
            "endDate": "2026-08-16T04:00Z",
        }],
        "leagues": [],
    }

    fake_response = Mock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = payload

    with patch("collector.requests.get", return_value=fake_response):
        tournament = discover_tournament_from_espn_api(
            datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
        )

    assert tournament is not None
    assert tournament.name == "FedEx St. Jude Championship"
    assert tournament.start_date.isoformat() == "2026-08-13"
    assert tournament.end_date.isoformat() == "2026-08-16"
