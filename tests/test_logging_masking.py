import logging

from tastytrade_mcp.logging_utils import (
    AccountMaskingFilter,
    mask_account_number,
)


def test_mask_account_number():
    assert mask_account_number("5WT12345678") == "****5678"
    assert mask_account_number("123") == "123"  # too short to mask


def test_filter_masks_account_in_message():
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="placing order in account 5WT12345678 now",
        args=(),
        exc_info=None,
    )
    AccountMaskingFilter().filter(record)
    assert "5WT12345678" not in record.getMessage()
    assert "****5678" in record.getMessage()
