from trace2evals.dates import parse_explicit_date


def test_supported_dates_are_valid_unambiguous_and_respect_year():
    assert parse_explicit_date("June 19") == "2026-06-19"
    assert parse_explicit_date("June 19, 2027") == "2027-06-19"
    assert parse_explicit_date("2027-06-19") == "2027-06-19"
    for text in ("February 30", "2026-02-30", "June 19 or June 20", "soon"):
        assert parse_explicit_date(text) is None
