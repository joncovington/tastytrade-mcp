"""Command-line entry point.

Usage:
    tastytrade-mcp                      # run the MCP server over stdio (default)
    tastytrade-mcp --transport http     # run over HTTP (CORS + rate limited)
    tastytrade-mcp --enable-live-trading
    tastytrade-mcp secrets set          # store OAuth credentials in the keyring
    tastytrade-mcp secrets status       # show which credentials are stored
    tastytrade-mcp secrets delete       # remove stored credentials

Secret values are read via getpass and stored only in the OS keyring.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from rich.console import Console

from . import credentials
from .credentials import CredentialError

console = Console()


def _cmd_secrets_set(args: argparse.Namespace) -> int:
    console.print(
        f"[bold]Storing Tastytrade credentials in the keyring.[/] "
        f"([dim]backend: {credentials.get_backend_name()}[/])"
    )
    console.print("Leave a field blank to keep the existing value.\n")

    prompts = {
        credentials.CLIENT_SECRET: "OAuth client secret",
        credentials.REFRESH_TOKEN: "OAuth refresh token",
        credentials.ACCOUNT_NUMBER: "Default account number (optional)",
    }
    try:
        for key, label in prompts.items():
            secret = key != credentials.ACCOUNT_NUMBER
            value = (
                getpass.getpass(f"{label}: ")
                if secret
                else input(f"{label}: ")
            ).strip()
            if value:
                credentials.set_secret(key, value)
                console.print(f"  [green]stored[/] {key}")
            else:
                console.print(f"  [dim]skipped[/] {key}")
    except CredentialError as exc:
        console.print(f"\n[red]Keyring error:[/] {exc}")
        return 1

    if credentials.secrets_present():
        console.print("\n[green]OK[/] Credentials are ready.")
        return 0
    missing = ", ".join(credentials.missing_secrets())
    console.print(f"\n[yellow]![/] Still missing required: {missing}")  # noqa: RUF001
    return 1


def _cmd_secrets_status(_args: argparse.Namespace) -> int:
    console.print(
        f"[bold]Tastytrade credentials[/] "
        f"([dim]backend: {credentials.get_backend_name()}[/])"
    )
    try:
        for key in credentials.ALL_SECRETS:
            present = credentials.get_secret(key) is not None
            mark = "[green]set[/]" if present else "[red]missing[/]"
            console.print(f"  {key}: {mark}")
    except CredentialError as exc:
        console.print(f"  [red]Keyring error:[/] {exc}")
        return 1
    return 0 if credentials.secrets_present() else 1


def _cmd_secrets_delete(_args: argparse.Namespace) -> int:
    try:
        removed = [
            key for key in credentials.ALL_SECRETS if credentials.delete_secret(key)
        ]
    except CredentialError as exc:
        console.print(f"[red]Keyring error:[/] {exc}")
        return 1
    if removed:
        console.print(f"[green]Removed credentials:[/] {', '.join(removed)}")
    else:
        console.print("[dim]No credentials were stored.[/]")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.enable_live_trading:
        os.environ["ENABLE_LIVE_TRADING"] = "true"

    # Imported lazily so `secrets` commands don't pay the MCP import cost.
    from .server import run

    run(transport=args.transport)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tastytrade-mcp",
        description="Tastytrade MCP server for autonomous AI agents.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport to run the server with (default: stdio).",
    )
    parser.add_argument(
        "--enable-live-trading",
        action="store_true",
        help="Register order-placing tools.",
    )

    sub = parser.add_subparsers(dest="command")

    secrets = sub.add_parser("secrets", help="Manage stored OAuth credentials.")
    secrets_sub = secrets.add_subparsers(dest="secrets_command", required=True)
    secrets_sub.add_parser(
        "set", help="Store credentials."
    ).set_defaults(func=_cmd_secrets_set)
    secrets_sub.add_parser(
        "status", help="Show stored credentials."
    ).set_defaults(func=_cmd_secrets_status)
    secrets_sub.add_parser(
        "delete", help="Remove credentials."
    ).set_defaults(func=_cmd_secrets_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "secrets":
        return args.func(args)

    # No subcommand → run the server.
    return _cmd_serve(args)


if __name__ == "__main__":
    sys.exit(main())
