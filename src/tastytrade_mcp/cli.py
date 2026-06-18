"""Command-line entry point.

Usage:
    tastytrade-mcp                      # run the MCP server over stdio (default)
    tastytrade-mcp --transport http     # run over HTTP (CORS + rate limited)
    tastytrade-mcp secrets set          # store OAuth credentials in the keyring
    tastytrade-mcp secrets status       # show which credentials are stored
    tastytrade-mcp secrets delete       # remove stored credentials

Secret values are read via getpass and stored only in the OS keyring.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from rich.console import Console

from . import credentials
from .config import get_config

console = Console()


def _env_flag(args: argparse.Namespace) -> bool:
    """Resolve the sandbox flag: --sandbox/--production override config."""
    if getattr(args, "sandbox", False):
        return True
    if getattr(args, "production", False):
        return False
    return get_config().sandbox


def _cmd_secrets_set(args: argparse.Namespace) -> int:
    sandbox = _env_flag(args)
    env = "sandbox" if sandbox else "production"
    console.print(f"[bold]Storing {env} Tastytrade credentials in the keyring.[/]")
    console.print("Leave a field blank to keep the existing value.\n")

    prompts = {
        credentials.CLIENT_SECRET: "OAuth client secret",
        credentials.REFRESH_TOKEN: "OAuth refresh token",
        credentials.ACCOUNT_NUMBER: "Default account number (optional)",
    }
    for key, label in prompts.items():
        secret = key != credentials.ACCOUNT_NUMBER
        value = (
            getpass.getpass(f"{label}: ")
            if secret
            else input(f"{label}: ")
        ).strip()
        if value:
            credentials.set_secret(key, value, sandbox=sandbox)
            console.print(f"  [green]stored[/] {key}")
        else:
            console.print(f"  [dim]skipped[/] {key}")

    if credentials.secrets_present(sandbox=sandbox):
        console.print(f"\n[green]OK[/] {env} credentials are ready.")
        return 0
    missing = ", ".join(credentials.missing_secrets(sandbox=sandbox))
    console.print(f"\n[yellow]![/] Still missing required: {missing}")  # noqa: RUF001
    return 1


def _cmd_secrets_status(args: argparse.Namespace) -> int:
    sandbox = _env_flag(args)
    env = "sandbox" if sandbox else "production"
    console.print(f"[bold]{env} credentials[/]")
    for key in credentials.ALL_SECRETS:
        present = credentials.get_secret(key, sandbox=sandbox) is not None
        mark = "[green]set[/]" if present else "[red]missing[/]"
        console.print(f"  {key}: {mark}")
    return 0 if credentials.secrets_present(sandbox=sandbox) else 1


def _cmd_secrets_delete(args: argparse.Namespace) -> int:
    sandbox = _env_flag(args)
    env = "sandbox" if sandbox else "production"
    removed = [
        key
        for key in credentials.ALL_SECRETS
        if credentials.delete_secret(key, sandbox=sandbox)
    ]
    if removed:
        console.print(f"[green]Removed {env} secrets:[/] {', '.join(removed)}")
    else:
        console.print(f"[dim]No {env} secrets were stored.[/]")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
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

    sub = parser.add_subparsers(dest="command")

    # Parent parser so --sandbox/--production work on every leaf subcommand.
    env_flags = argparse.ArgumentParser(add_help=False)
    env_flags.add_argument("--sandbox", action="store_true", help="Target sandbox.")
    env_flags.add_argument(
        "--production", action="store_true", help="Target production."
    )

    secrets = sub.add_parser("secrets", help="Manage stored OAuth credentials.")
    secrets_sub = secrets.add_subparsers(dest="secrets_command", required=True)
    secrets_sub.add_parser(
        "set", help="Store credentials.", parents=[env_flags]
    ).set_defaults(func=_cmd_secrets_set)
    secrets_sub.add_parser(
        "status", help="Show stored credentials.", parents=[env_flags]
    ).set_defaults(func=_cmd_secrets_status)
    secrets_sub.add_parser(
        "delete", help="Remove credentials.", parents=[env_flags]
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
