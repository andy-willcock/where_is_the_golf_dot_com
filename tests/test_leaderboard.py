from leaderboard import parse_cbs_leaderboard

def test_cbs_leaderboard_top15():
    rows = []
    for i in range(1, 21):
        rows.append(
            f"""
            <tr>
              <td>{i}</td>
              <td>USA</td>
              <td><a>P. {i}</a><a>Player {i}</a></td>
              <td>-{i}</td>
              <td>{min(i, 18)}</td>
              <td>-1</td>
              <td>69</td><td>-</td><td>-</td><td>-</td><td>-</td>
            </tr>
            """
        )

    html = f"""
    <html>
      <head><title>2026 BMW Championship Leaderboard - CBS Sports</title></head>
      <body>
        <table>
          <tr>
            <th>pos</th><th>ctry</th><th>name</th><th>to par</th>
            <th>thru</th><th>today</th><th>r1</th><th>r2</th>
            <th>r3</th><th>r4</th><th>total</th>
          </tr>
          {''.join(rows)}
        </table>
      </body>
    </html>
    """

    payload = parse_cbs_leaderboard(html)

    assert payload["tournament"] == "BMW Championship"
    assert len(payload["players"]) == 15
    assert payload["players"][0]["player"] == "Player 1"
    assert payload["players"][14]["position"] == "15"
