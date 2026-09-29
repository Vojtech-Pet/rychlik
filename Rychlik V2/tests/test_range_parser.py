from rychlik.share.range_parser import RangeOutcome, parse_range

SIZE = 1000


def test_no_range_header():
    result = parse_range(None, SIZE)
    assert result.outcome == RangeOutcome.NONE


def test_exact_single_byte():
    result = parse_range("bytes=0-0", SIZE)
    assert result.outcome == RangeOutcome.SINGLE
    assert (result.start, result.end) == (0, 0)


def test_exact_range():
    result = parse_range("bytes=0-99", SIZE)
    assert (result.start, result.end) == (0, 99)


def test_open_ended_range():
    result = parse_range("bytes=100-", SIZE)
    assert result.outcome == RangeOutcome.SINGLE
    assert (result.start, result.end) == (100, SIZE - 1)


def test_suffix_range():
    result = parse_range("bytes=-500", SIZE)
    assert (result.start, result.end) == (SIZE - 500, SIZE - 1)


def test_suffix_range_larger_than_file():
    result = parse_range("bytes=-5000", SIZE)
    assert (result.start, result.end) == (0, SIZE - 1)


def test_last_byte_range():
    result = parse_range(f"bytes={SIZE - 1}-{SIZE - 1}", SIZE)
    assert (result.start, result.end) == (SIZE - 1, SIZE - 1)


def test_end_clamped_when_beyond_eof():
    result = parse_range(f"bytes=0-{SIZE + 500}", SIZE)
    assert result.outcome == RangeOutcome.SINGLE
    assert (result.start, result.end) == (0, SIZE - 1)


def test_start_greater_than_end_is_unsatisfiable():
    result = parse_range("bytes=500-100", SIZE)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_start_beyond_eof_is_unsatisfiable():
    result = parse_range(f"bytes={SIZE}-{SIZE + 10}", SIZE)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_open_ended_start_beyond_eof_is_unsatisfiable():
    result = parse_range(f"bytes={SIZE}-", SIZE)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_zero_length_suffix_is_unsatisfiable():
    result = parse_range("bytes=-0", SIZE)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_invalid_unit_is_ignored():
    result = parse_range("items=0-10", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_empty_spec_is_ignored():
    result = parse_range("bytes=", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_no_dash_is_ignored():
    result = parse_range("bytes=100", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_multiple_ranges_are_ignored():
    result = parse_range("bytes=0-99,200-299", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_negative_malformed_start_is_ignored():
    result = parse_range("bytes=-100-200", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_whitespace_in_spec_is_ignored():
    result = parse_range("bytes= 0 - 99 ", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_no_equals_sign_is_ignored():
    result = parse_range("bytes 0-99", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_overflow_like_huge_integer_is_ignored():
    huge = "9" * 40
    result = parse_range(f"bytes={huge}-", SIZE)
    assert result.outcome == RangeOutcome.IGNORED


def test_overflow_like_but_reasonable_huge_integer_is_unsatisfiable():
    huge = "9" * 18
    result = parse_range(f"bytes={huge}-", SIZE)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_zero_length_file_no_range_is_none():
    result = parse_range(None, 0)
    assert result.outcome == RangeOutcome.NONE


def test_zero_length_file_with_range_is_unsatisfiable():
    result = parse_range("bytes=0-0", 0)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_zero_length_file_suffix_range_is_unsatisfiable():
    result = parse_range("bytes=-1", 0)
    assert result.outcome == RangeOutcome.UNSATISFIABLE


def test_zero_length_file_open_ended_is_unsatisfiable():
    result = parse_range("bytes=0-", 0)
    assert result.outcome == RangeOutcome.UNSATISFIABLE
