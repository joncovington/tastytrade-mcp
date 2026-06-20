import pytest

from tastytrade_mcp.config import Config
from tastytrade_mcp.server import build_server


def _config(live: bool):
    return Config(
        sandbox=True,
        enable_live_trading=live,
        force_dry_run=False,
        buying_power_buffer_pct=0.0,
        account_deploy_limit_pct=0.0,
        log_level="INFO",
        cors_origin="http://localhost:3333",
        rate_limit="120/minute",
        http_host="127.0.0.1",
        http_port=7698,
    )


async def _tool_names(config):
    mcp = build_server(config)
    tools = await mcp.list_tools()
    return {t.name for t in tools}


READ_TOOLS = {
    "get_connection_status",
    "get_market_overview",
    "get_option_chain",
    "get_strategies",
    "get_account_info",
    "get_positions",
    "list_accounts",
    "get_working_orders",
    "get_watchlists",
}
WRITE_TOOLS = {"execute_trade", "adjust_order", "close_position", "manage_watchlist"}


@pytest.mark.asyncio
async def test_read_tools_present_when_live_trading_disabled():
    names = await _tool_names(_config(live=False))
    assert READ_TOOLS <= names
    assert not (WRITE_TOOLS & names)  # gated tools absent


@pytest.mark.asyncio
async def test_write_tools_present_when_live_trading_enabled():
    names = await _tool_names(_config(live=True))
    assert WRITE_TOOLS <= names
