from datetime import date
from collector import Tournament, parse_viewing_guide, parse_time_range, EASTERN

def fixture_html():
    text = open("tests/fixtures/viewing_guide.txt", encoding="utf-8").read()
    return "<html><body>" + "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip()) + "</body></html>"

def tournament():
    return Tournament(
        name="FedEx St. Jude Championship",
        start_date=date(2026, 8, 13),
        end_date=date(2026, 8, 16),
    )

def test_infers_meridiem():
    parsed = parse_time_range("Early TV coverage: 1-3 p.m. on Golf Channel", date(2026,8,15))
    assert parsed is not None
    start, end = parsed
    assert start.astimezone(EASTERN).hour == 13
    assert end.astimezone(EASTERN).hour == 15

def test_parses_multiple_providers():
    items = parse_viewing_guide(fixture_html(), tournament(), "https://www.cbssports.com/golf/news/example/")
    providers = {x.provider for x in items}
    assert "CBS" in providers
    assert "Paramount+" in providers
    assert "Golf Channel" in providers
    assert "ESPN" in providers

def test_has_final_round():
    items = parse_viewing_guide(fixture_html(), tournament(), "https://www.cbssports.com/golf/news/example/")
    assert any(x.round == "Final Round" for x in items)
