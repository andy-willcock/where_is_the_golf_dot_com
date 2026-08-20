import pandas as pd

from leaderboard import choose_tournament_id, build_payload_from_current_leaders


def test_choose_current_tournament_id_by_name():
    schedule = pd.DataFrame([
        {
            "tournament_id": "R2026027",
            "tournament_name": "BMW Championship",
            "status": "IN_PROGRESS",
        },
        {
            "tournament_id": "R2026030",
            "tournament_name": "TOUR Championship",
            "status": "UPCOMING",
        },
    ])

    tournament_id, name = choose_tournament_id(
        schedule,
        "BMW Championship",
    )

    assert tournament_id == "R2026027"
    assert name == "BMW Championship"


def test_choose_current_tournament_id_with_small_name_difference():
    schedule = pd.DataFrame([
        {
            "tournament_id": "R2026027",
            "tournament_name": "BMW Championship",
            "status": "IN_PROGRESS",
        },
    ])

    tournament_id, _ = choose_tournament_id(
        schedule,
        "2026 BMW Championship",
    )

    assert tournament_id == "R2026027"


def test_build_top15_payload():
    rows = []
    for i in range(1, 20):
        rows.append({
            "display_name": f"Player {i}",
            "position": str(i),
            "total_score": f"-{i}",
            "thru": str(min(i, 18)),
            "round_score": "-1",
        })

    df = pd.DataFrame(rows)

    payload = build_payload_from_current_leaders(
        df,
        "BMW Championship",
        "R2026027",
    )

    assert payload["tournament"] == "BMW Championship"
    assert payload["tournamentId"] == "R2026027"
    assert len(payload["players"]) == 15
    assert payload["players"][0]["player"] == "Player 1"
    assert payload["players"][14]["position"] == "15"
