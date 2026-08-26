"""
    Copyright (C) 2025-26 Dipl.-Ing. Christoph Massmann <chris@dev-investor.de>

    This file is part of pp-terminal.

    pp-terminal is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
"""

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from _pytest.fixtures import TopRequest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from pp_terminal.mcp_server import create_mcp_server
from pp_terminal.utils.config import empty_config


@pytest.fixture(name='mcp')
def provide_mcp_server(request: TopRequest) -> FastMCP:
    xml_file = Path(request.path.parent / 'fixtures' / 'kommer.ids.xml')
    return create_mcp_server(xml_file, empty_config())


def _call(mcp: FastMCP, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    """Invokes a tool the way a client does and returns its structured result."""
    _, structured = cast(tuple[Any, dict[str, Any]], asyncio.run(mcp.call_tool(tool, arguments or {})))
    return structured['result']


def test_query_cash_flows_reports_every_currency(mcp: FastMCP) -> None:
    assert _call(mcp, 'query_cash_flows') == [
        {'currency': 'EUR', 'totalDeposits': 11500.0, 'totalWithdrawals': 0.0, 'netContributions': 11500.0, 'transactionCount': 3},
        {'currency': 'GBP', 'totalDeposits': 2000.0, 'totalWithdrawals': 0.0, 'netContributions': 2000.0, 'transactionCount': 1},
        {'currency': 'USD', 'totalDeposits': 3000.0, 'totalWithdrawals': 0.0, 'netContributions': 3000.0, 'transactionCount': 1},
    ]


def test_query_cash_flows_honours_the_date_argument(mcp: FastMCP) -> None:
    """The two 2019 deposits count, the later ones do not."""
    assert _call(mcp, 'query_cash_flows', {'date': '2020-01-01'}) == [
        {'currency': 'EUR', 'totalDeposits': 10500.0, 'totalWithdrawals': 0.0, 'netContributions': 10500.0, 'transactionCount': 2},
    ]


def test_query_cash_flows_restricts_to_an_account_given_by_name(mcp: FastMCP) -> None:
    assert _call(mcp, 'query_cash_flows', {'account_id': 'Tagesgeld'}) == [
        {'currency': 'EUR', 'totalDeposits': 500.0, 'totalWithdrawals': 0.0, 'netContributions': 500.0, 'transactionCount': 1},
    ]


def test_query_cash_flows_restricts_to_an_account_given_by_id(mcp: FastMCP) -> None:
    assert _call(mcp, 'query_cash_flows', {'account_id': 'ea9414e0-1787-46c0-92b3-8e2370eb892e'}) == [
        {'currency': 'EUR', 'totalDeposits': 500.0, 'totalWithdrawals': 0.0, 'netContributions': 500.0, 'transactionCount': 1},
    ]


def test_query_cash_flows_reports_an_unknown_account_instead_of_an_empty_result(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match="Deposit account 'Girokonto' not found"):
        _call(mcp, 'query_cash_flows', {'account_id': 'Girokonto'})


def test_query_cash_flows_is_offered_to_the_model(mcp: FastMCP) -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert 'query_cash_flows' in tools
    assert set(tools['query_cash_flows'].inputSchema['properties']) == {'date', 'account_id', 'include_transfers'}
