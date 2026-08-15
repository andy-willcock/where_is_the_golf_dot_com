from datetime import datetime, timezone
from collector import parse_media_broadcast_schedule

def record(tournament, round_, date_, airtime, network, content="Full Telecast"):
    return f"""
    <p>Tournament: {tournament}</p>
    <p>Round: {round_}</p>
    <p>Date: {date_}</p>
    <p>Airtime: {airtime}</p>
    <p>Network: {network}</p>
    <p>Content Type: {content}</p>
    """

def test_media_schedule_keeps_saturday_and_sunday():
    name = "FedEx St. Jude Championship"
    html = "<html><body><h5>PGA TOUR</h5>"

    html += record(name, 1, "TH 08/13", "8:00 AM - 2:00 PM Eastern", "ESPN+")
    html += record(name, 1, "TH 08/13", "2:00 PM - 6:00 PM Eastern", "GOLF Channel")
    html += record(name, 2, "FR 08/14", "8:00 AM - 2:00 PM Eastern", "ESPN+")
    html += record(name, 2, "FR 08/14", "2:00 PM - 6:00 PM Eastern", "GOLF Channel")

    html += record(name, 3, "SA 08/15", "7:45 AM - 1:00 PM Eastern", "ESPN+")
    html += record(name, 3, "SA 08/15", "8:15 AM - 1:00 PM Eastern", "PGA TOUR LIVE Betcast presented by DraftKings")
    html += record(name, 3, "SA 08/15", "1:00 PM - 3:00 PM Eastern", "GOLF Channel")
    html += record(name, 3, "SA 08/15", "1:00 PM - 6:00 PM Eastern", "SiriusXM", "Audio")
    html += record(name, 3, "SA 08/15", "3:00 PM - 6:00 PM Eastern", "Paramount+")
    html += record(name, 3, "SA 08/15", "3:00 PM - 6:00 PM Eastern", "CBS")

    html += record(name, 4, "SU 08/16", "7:45 AM - 12:00 PM Eastern", "ESPN+")
    html += record(name, 4, "SU 08/16", "12:00 PM - 2:00 PM Eastern", "GOLF Channel")
    html += record(name, 4, "SU 08/16", "2:00 PM - 6:00 PM Eastern", "Paramount+")
    html += record(name, 4, "SU 08/16", "2:00 PM - 6:00 PM Eastern", "CBS")

    html += "<h5>PGA TOUR Champions</h5>"
    html += record("Boeing Classic", 2, "SA 08/15", "5:00 PM - 6:00 PM Eastern", "GOLF Channel")
    html += "</body></html>"

    tournament, coverage = parse_media_broadcast_schedule(
        html,
        now=datetime(2026, 8, 15, 21, 0, tzinfo=timezone.utc),
    )

    assert tournament.name == name

    round3 = [x for x in coverage if x.round == "Round 3"]
    round4 = [x for x in coverage if x.round == "Final Round"]

    assert len(round3) == 5
    assert len(round4) == 4

    saturday_providers = {x.provider for x in round3}
    assert saturday_providers == {
        "ESPN+",
        "PGA TOUR LIVE Betcast presented by DraftKings",
        "Golf Channel",
        "Paramount+",
        "CBS",
    }

    assert "SiriusXM" not in {x.provider for x in coverage}
    assert "Boeing Classic" != tournament.name
