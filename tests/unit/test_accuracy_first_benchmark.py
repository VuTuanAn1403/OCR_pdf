from benchmark.accuracy_first import _table_associations, normalize_row_label


def test_table_cell_scope_checks_row_and_column_without_penalizing_unannotated_rows():
    page = {"tables": [{"cells": [
        {"row_idx": 0, "col_idx": 0, "text": "Tổng giá trị các khoản phải thu quả hạn khác"},
        {"row_idx": 0, "col_idx": 1, "text": "35.296.305.783"},
        {"row_idx": 0, "col_idx": 2, "text": "9.912.807.558"},
        {"row_idx": 1, "col_idx": 0, "text": "Cộng"},
        {"row_idx": 1, "col_idx": 1, "text": "35.296.305.783"},
    ]}]}
    reference = "Tổng giá trị các khoản phải thu quá hạn"
    associations = _table_associations(page, {reference.casefold()})
    assert associations == {
        (reference.casefold(), 1, "35.296.305.783"),
        (reference.casefold(), 2, "9.912.807.558"),
    }
    assert normalize_row_label(reference) == normalize_row_label(
        "Tổng giá trị các khoản phải thu quả hạn"
    )


def test_table_cell_wrong_column_remains_an_error():
    page = {"tables": [{"cells": [
        {"row_idx": 0, "col_idx": 0, "text": "Tổng giá trị các khoản phải thu quá hạn"},
        {"row_idx": 0, "col_idx": 2, "text": "35.296.305.783"},
    ]}]}
    associations = _table_associations(page, {"tổng giá trị các khoản phải thu quá hạn"})
    assert ("tổng giá trị các khoản phải thu quá hạn", 1, "35.296.305.783") not in associations
