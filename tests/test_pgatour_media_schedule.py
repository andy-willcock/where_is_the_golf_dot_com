from datetime import datetime, timezone

from collector import parse_media_broadcast_schedule


def test_media_schedule_parses_current_week_and_excludes_audio():
    html = """
    <html><body>
    <h5>PGA TOUR</h5>
    <p>Tournament: FedEx St. Jude Championship</p>
    <p>Round: 3</p>
    <p>Date: SA 08/15</p>
    <p>Airtime: 7:45 AM - 1:00 PM Eastern</p>
    <p>Network: ESPN+</p>
    <p>Content Type: Full Telecast</p>

    <p>Tournament: FedEx St. Jude Championship</p>
    <p>Round: 3</p>
    <p>Date: SA 08/15</p>
    <p>Airtime: 1:00 PM - 3:00 PM Eastern</p>
    <p>Network: GOLF Channel</p>
    <p>Content Type: Full Telecast</p>

    <p>Tournament: FedEx St. Jude Championship</p>
    <p>Round: 3</p>
    <p>Date: SA 08/15</p>
    <p>Airtime: 1:00 PM - 6:00 PM Eastern</p>
    <p>Network: SiriusXM</p>
    <p>Content Type: Audio</p>

    <p>Tournament: FedEx St. Jude Championship</p>
    <p>Round: 3</p>
    <p>Date: SA 08/15</p>
    <p>Airtime: 3:00 PM - 6:00 PM Eastern</p>
    <p>Network: Paramount+</p>
    <p>Content Type: Full Telecast</p>

    <p>Tournament: FedEx St. Jude Championship</p>
    <p>Round: 3</p>
    <p>Date: SA 08/15</p>
    <p>Airtime: 3:00 PM - 6:00 PM Eastern</p>
    <p>Network: CBS</p>
    <p>Content Type: Full Telecast</p>

    <h5>PGA TOUR Champions</h5>
    <p>Tournament: Boeing Classic</p>
    </body></html>
    """

    tournament, coverage = parse_media_broadcast_schedule(
        html,
        now=datetime(2026, 8, 15, 21, 0, tzinfo=timezone.utc),
    )

    assert tournament is not None
    assert tournament.name == "FedEx St. Jude Championship"

    providers = {item.provider for item in coverage}
    assert providers == {"ESPN+", "Golf Channel", "Paramount+", "CBS"}

    types = {item.provider: item.type for item in coverage}
    assert types["ESPN+"] == "streaming"
    assert types["Paramount+"] == "streaming"
    assert types["CBS"] == "tv"

    assert all(item.provider != "SiriusXM" for item in coverage)
