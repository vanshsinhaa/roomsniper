from hayden_booker.logging_setup import redact_url


def test_url_query_and_fragment_are_redacted() -> None:
    assert redact_url("https://asu.libcal.com/r/accessible?lid=1&token=secret#x") == (
        "https://asu.libcal.com/r/accessible"
    )
