from tastytrade_mcp.server import build_server

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


async def _tool_names(config):
    tools = await build_server(config).list_tools()
    return {t.name for t in tools}


async def test_read_tools_present_when_live_trading_disabled(make_config):
    names = await _tool_names(make_config(enable_live_trading=False))
    assert READ_TOOLS <= names
    assert not (WRITE_TOOLS & names)  # gated tools absent


async def test_write_tools_present_when_live_trading_enabled(make_config):
    names = await _tool_names(make_config(enable_live_trading=True))
    assert WRITE_TOOLS <= names
