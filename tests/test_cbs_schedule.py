from datetime import date
from collector import parse_cbs_schedule, choose_active_tournament

def test_cbs_schedule_table_current_event():
    html = """
    <html><body>
    <table>
      <tr>
        <th>Date</th><th>Tournament</th><th>Location</th><th>Course</th>
      </tr>
      <tr>
        <td>Aug 6-9</td>
        <td>Wyndham Championship</td>
        <td>Greensboro, NC</td>
        <td>Sedgefield Country Club</td>
      </tr>
      <tr>
        <td>Aug 13-16</td>
        <td>FedEx St. Jude Championship</td>
        <td>Memphis, TN</td>
        <td>TPC Southwind</td>
      </tr>
      <tr>
        <td>Aug 20-23</td>
        <td>BMW Championship</td>
        <td>St. Louis, MO</td>
        <td>Bellerive Country Club</td>
      </tr>
    </table>
    </body></html>
    """

    events = parse_cbs_schedule(
        html,
        2026,
        "https://www.cbssports.com/golf/schedules/",
    )
    active = choose_active_tournament(events, date(2026, 8, 15))

    assert active is not None
    assert active.name == "FedEx St. Jude Championship"
    assert active.course == "TPC Southwind"
    assert active.location == "Memphis, TN"
    assert active.start_date == date(2026, 8, 13)
    assert active.end_date == date(2026, 8, 16)
