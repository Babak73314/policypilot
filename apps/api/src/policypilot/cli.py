"""The `policypilot` command: database, seed, tokens, customers and maintenance."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from policypilot.compat import run
from policypilot.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="PolicyPilot operations.")
db_app = typer.Typer(no_args_is_help=True, help="Database schema.")
tokens_app = typer.Typer(no_args_is_help=True, help="API tokens for MCP clients and n8n.")
customers_app = typer.Typer(no_args_is_help=True, help="Data-subject requests.")
maintenance_app = typer.Typer(no_args_is_help=True, help="Retention and recovery jobs.")
app.add_typer(db_app, name="db")
app.add_typer(tokens_app, name="tokens")
app.add_typer(customers_app, name="customers")
app.add_typer(maintenance_app, name="maintenance")

API_ROOT = Path(__file__).resolve().parents[2]


def _run(factory: Callable[[], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        return await factory()

    return run(main())


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply Alembic migrations, then create the LangGraph checkpoint tables."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(config, "head")

    async def setup_checkpointer() -> None:
        from policypilot.agents.checkpoint import open_checkpointer

        async with open_checkpointer(get_settings()) as (saver, _pool):
            await saver.setup()

    _run(setup_checkpointer)
    typer.echo("database is at head; checkpoint tables are ready")


@app.command("seed")
def seed(
    reindex: Annotated[
        bool, typer.Option("--reindex", help="Re-embed every document, even unchanged ones.")
    ] = False,
) -> None:
    """Load the fictional catalog, render and ingest policy PDFs, create the demo agent."""
    from policypilot.db.session import Database
    from policypilot.rag.embeddings import build_embedder
    from policypilot.seed.run import run_seed

    async def go() -> Any:
        settings = get_settings()
        db = Database(settings)
        try:
            return await run_seed(db, settings, build_embedder(settings), reindex=reindex)
        finally:
            await db.dispose()

    report = _run(go)
    typer.echo(json.dumps(report.__dict__, indent=2, default=str))


@tokens_app.command("create")
def tokens_create(
    name: str,
    scopes: list[str] = typer.Option(..., "--scope", help="mcp:read, mcp:write, integrations"),  # noqa: B008
) -> None:
    """Create an API token. The secret is printed once and never stored."""
    from policypilot.db.session import Database
    from policypilot.services.tokens import create_token

    async def go() -> str:
        db = Database(get_settings())
        try:
            async with db.transaction() as session:
                return await create_token(session, name=name, scopes=scopes, created_by="cli")
        finally:
            await db.dispose()

    typer.echo(_run(go))


@tokens_app.command("revoke")
def tokens_revoke(prefix: str) -> None:
    """Revoke every token whose prefix matches."""
    from policypilot.db.session import Database
    from policypilot.services.tokens import revoke_tokens

    async def go() -> int:
        db = Database(get_settings())
        try:
            async with db.transaction() as session:
                return await revoke_tokens(session, prefix)
        finally:
            await db.dispose()

    typer.echo(f"revoked {_run(go)} token(s)")


@customers_app.command("export")
def customers_export(customer_id: uuid.UUID, output: Path) -> None:
    """Export everything stored about a customer (erasure inventory) to a JSON file."""
    from policypilot.db.session import Database
    from policypilot.services.privacy import export_customer

    async def go() -> dict[str, Any]:
        db = Database(get_settings())
        try:
            return await export_customer(db, customer_id)
        finally:
            await db.dispose()

    output.write_text(json.dumps(_run(go), indent=2, default=str), encoding="utf-8")
    typer.echo(f"wrote {output}")


@customers_app.command("erase")
def customers_erase(customer_id: uuid.UUID) -> None:
    """Erase a customer across every store, including LangGraph checkpoints."""
    from policypilot.db.session import Database
    from policypilot.services.privacy import erase_customer

    async def go() -> dict[str, int]:
        settings = get_settings()
        db = Database(settings)
        try:
            return await erase_customer(db, settings, customer_id)
        finally:
            await db.dispose()

    typer.echo(json.dumps(_run(go), indent=2))


@maintenance_app.command("run")
def maintenance_run(
    job: str = typer.Argument(
        "all",
        help="all | close-inactive | purge-conversations | purge-sales | purge-runs | purge-outbox",
    ),
) -> None:
    """Run retention and recovery jobs (data-model.md#retention-and-erasure)."""
    from policypilot.services.maintenance import run_jobs

    typer.echo(json.dumps(_run(lambda: run_jobs(get_settings(), job)), indent=2))


if __name__ == "__main__":
    app()
