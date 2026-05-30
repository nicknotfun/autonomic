from amp.exceptions import ParseUnderflowError


def test_parse_underflow_error_is_value_error() -> None:
    error = ParseUnderflowError("short")

    assert isinstance(error, ValueError)
    assert str(error) == "short"
