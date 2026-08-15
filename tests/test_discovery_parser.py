from datetime import date
from collector import parse_espn_schedule, choose_active_tournament

def test_espn_collapsed_schedule_rows():
    html = """
    <html><body>
    <div>
      Aug 6 - 9 Wyndham Championship Sedgefield Country Club - Greensboro, NC
      Aug 13 - 16 FedEx St. Jude Championship TPC Southwind - Memphis, TN
      Aug 20 - 23 BMW Championship Bellerive Country Club - St. Louis, MO
      Aug 27 - 30 TOUR Championship East Lake Golf Club - Atlanta, GA
    </div>
    </body></html>
    """

    events = parse_espn_schedule(
        html,
        2026,
        "https://www.espn.com/golf/schedule",
    )

    active = choose_active_tournament(events, date(2026, 8, 15))

    assert active is not None
    assert "FedEx St. Jude Championship" in active.name
    assert active.start_date == date(2026, 8, 13)
    assert active.end_date == date(2026, 8, 16)

def test_espn_separate_nodes_still_work():
    html = """
    <html><body>
      <div>Aug 13 - 16</div>
      <div>FedEx St. Jude Championship</div>
      <div>TPC Southwind - Memphis, TN</div>
    </body></html>
    """

    events = parse_espn_schedule(
        html,
        2026,
        "https://www.espn.com/golf/schedule",
    )
    active = choose_active_tournament(events, date(2026, 8, 15))

    assert active is not None
    assert "FedEx St. Jude Championship" in active.name
