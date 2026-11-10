"""Result-set comparison: every correct query must pass, plausible wrong ones must fail."""

import pytest

from askoract.eval.compare import Tolerance, compare_results, normalize

GOLD = [("Germany", 11), ("France", 11), ("USA", 13)]


def match(gold, pred, **kw):
    return compare_results(gold, pred, **kw).match


def test_identical():
    assert match(GOLD, list(GOLD))


def test_row_order_ignored_by_default():
    assert match(GOLD, list(reversed(GOLD)))


def test_row_order_enforced_when_ordered():
    assert not match(GOLD, list(reversed(GOLD)), ordered=True)
    assert match(GOLD, list(GOLD), ordered=True)


def test_column_order_and_names_ignored():
    assert match(GOLD, [(n, c) for c, n in GOLD])


def test_extra_predicted_columns_allowed():
    pred = [(i, c, n, "x") for i, (c, n) in enumerate(GOLD)]
    cmp = compare_results(GOLD, pred)
    assert cmp.match and cmp.column_map == [1, 2]


def test_missing_column_fails():
    assert not match(GOLD, [(c,) for c, _ in GOLD])


def test_row_count_mismatch_fails_with_reason():
    cmp = compare_results(GOLD, GOLD[:2])
    assert not cmp.match and "row count" in cmp.reason


def test_extra_row_fails():
    assert not match([(5,)], [(5,), (6,)])


def test_duplicates_are_a_multiset():
    assert not match([(1,), (1,), (2,)], [(1,), (2,), (2,)])
    assert match([(1,), (1,), (2,)], [(2,), (1,), (1,)])


def test_float_rounded_to_cents_passes():
    assert match([(40209904.2335,)], [(40209904.23,)])
    assert match([(28.866363636,)], [(28.87,)])


def test_float_meaningfully_off_fails():
    # e.g. a missed cancelled-order filter shifts revenue by several percent
    assert not match([(36628629.85,)], [(39742066.18,)])
    assert not match([(28.866,)], [(26.734,)])


def test_integers_compare_exactly():
    assert not match([(16282,)], [(16283,)])
    assert match([(16282,)], [(16282.0,)])


def test_numeric_strings_equal_numbers():
    assert match([("2012", 654)], [(2012, "654")])
    assert match([("01", 5)], [(1, 5)])


def test_strings_are_stripped_but_case_sensitive():
    assert match([("Val2",)], [("Val2 ",)])
    assert not match([("chai",)], [("Chai",)])


def test_none_only_equals_none():
    assert match([(None, 2)], [(None, 2)])
    assert not match([(None,)], [(0,)])


def test_custom_tolerance():
    assert not match([(0.000199,)], [(0.0,)], tol=Tolerance(abs=1e-6))
    assert match([(0.000199,)], [(0.0,)])  # default abs_tol 0.01 would hide this


def test_duplicate_columns_in_prediction_are_mapped_injectively():
    gold = [(1, 1), (2, 2)]
    assert match(gold, [(1, 1), (2, 2)])
    assert not match([(1, 2)], [(1, 9)])


def test_both_empty():
    assert match([], [])


def test_tolerance_alignment_after_sorting():
    # 1.004 and 1.006 sort differently from 1.0049/1.0051 but all match within tolerance
    gold = [(1.004, "a"), (1.006, "b")]
    pred = [(1.0051, "a"), (1.0049, "b")]
    assert match(gold, pred)


@pytest.mark.parametrize(
    "raw, expected", [("12", 12), (" 3.5 ", 3.5), ("abc ", "abc"), (True, 1), (None, None)]
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected
