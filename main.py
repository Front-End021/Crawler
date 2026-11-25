"""CLI Entrypoint for Autonomous AI Web Crawler & Postman Collection Exporter."""

import asyncio
from pathlib import Path
import sys
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from src.ai_mapper.endpoint_processor import EndpointProcessor
from src.config import get_settings
from src.crawler.agent_runner import AgentRunner, CrawlerResult
from src.exporter.postman_exporter import PostmanExporter

app = typer.Typer(
    name="api-crawler",
    help="Autonomous AI Web Crawler & Postman Collection Exporter",
    add_completion=False,
)
console = Console()


def print_banner() -> None:
    """Display modern CLI banner."""
    banner_text = (
        "[bold cyan]⚡ Autonomous AI Web Crawler & Postman Exporter ⚡[/bold cyan]\n"
        "[dim]Intercepts API traffic, correlates UI actions, enriches via Gemini, and exports Postman v2.1[/dim]"
    )
    console.print(Panel(banner_text, border_style="cyan", expand=False))


def validate_target_url(url: str) -> bool:
    """Validate that target URL has a valid HTTP/HTTPS scheme and network location."""
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme in ("http", "https") and parsed.netloc)
    except Exception:
        return False


def display_configuration(
    url: str,
    username: Optional[str],
    password: Optional[str],
    max_steps: int,
    headless: bool,
    output_dir: Path,
    model: str,
) -> None:
    """Display runtime parameters in a secure Rich table (masking credentials)."""
    table = Table(title="Execution Parameters", show_header=True, header_style="bold magenta")
    table.add_column("Parameter", style="cyan", width=22)
    table.add_column("Value", style="green")

    table.add_row("Target URL", url)
    if username and password:
        auth_display = "Yes (Configured)"
    elif username:
        auth_display = "[yellow]Incomplete (Missing Password - Skipped)[/yellow]"
    else:
        auth_display = "No (Public Access)"
    table.add_row("Authentication", auth_display)
    if username:
        table.add_row("Username", username)
        table.add_row("Password", "[bold yellow]********[/bold yellow]" if password else "[italic dim]None[/italic dim]")
    table.add_row("Max AI Steps", str(max_steps))
    table.add_row("Browser Mode", "Headless" if headless else "Headed (Live View)")
    table.add_row("Output Directory", str(output_dir))
    table.add_row("Gemini Model", model)

    console.print(table)


def display_final_summary(
    target_url: str,
    crawl_result: CrawlerResult,
    dedup_count: int,
    output_path: Path,
    schema_valid: bool,
) -> None:
    """Display final execution metrics and exported collection details in a styled Rich table."""
    table = Table(title="🎉 Crawl & Export Pipeline Summary", show_header=True, header_style="bold green")
    table.add_column("Metric", style="cyan", width=30)
    table.add_column("Result", style="bold white")

    table.add_row("Target Website", target_url)
    table.add_row("Authentication Result", "Authenticated ✓" if crawl_result.auth_success else ("N/A (Public)" if crawl_result.auth_success is None else "Failed ✗"))
    table.add_row("Navigation Tabs Explored", str(len(crawl_result.tabs_visited)))
    table.add_row("Total UI Actions Executed", str(crawl_result.actions_executed))
    table.add_row("Destructive Actions Blocked", f"[bold red]{crawl_result.destructive_actions_blocked}[/bold red]")
    table.add_row("Raw API Calls Intercepted", str(len(crawl_result.captured_endpoints)))
    table.add_row("Deduplicated Endpoints Exported", f"[bold green]{dedup_count}[/bold green]")
    table.add_row("Postman Collection v2.1 Schema", "[bold green]PASS (100% Compliant)[/bold green]" if schema_valid else "[bold red]FAIL[/bold red]")
    table.add_row("Output Collection File", str(output_path))

    console.print(table)


async def run_pipeline(
    url: str,
    username: Optional[str],
    password: Optional[str],
    max_steps: int,
    headless: bool,
    output_dir: Path,
) -> Path:
    """Execute complete end-to-end crawling, enrichment, and export pipeline."""
    settings = get_settings()

    # 1. Autonomous Crawl & Interception
    runner = AgentRunner(settings=settings)
    with console.status("[bold cyan]Crawling web application & intercepting API calls...[/bold cyan]"):
        crawl_result = await runner.crawl(
            target_url=url,
            username=username,
            password=password,
            max_steps=max_steps,
            headless=headless,
        )

    # 2. AI Deduplication & Renaming
    processor = EndpointProcessor(settings=settings)
    with console.status("[bold cyan]Deduplicating & enriching endpoints via Gemini...[/bold cyan]"):
        mapped_endpoints = await processor.process_and_enrich(crawl_result.captured_endpoints)

    # 3. Postman Collection Export
    exporter = PostmanExporter()
    with console.status("[bold cyan]Building & validating Postman Collection v2.1...[/bold cyan]"):
        collection_data = exporter.build_collection(mapped_endpoints, target_url=url)
        output_file = exporter.export_to_file(
            mapped_endpoints=mapped_endpoints,
            target_url=url,
            output_dir=output_dir,
            collection_data=collection_data,
        )
        schema_valid = exporter.validate_schema(collection_data)

    # 4. Display Final Summary
    display_final_summary(
        target_url=url,
        crawl_result=crawl_result,
        dedup_count=len(mapped_endpoints),
        output_path=output_file,
        schema_valid=schema_valid,
    )

    return output_file


@app.command()
def crawl(
    url: str = typer.Option(
        ...,
        "--url",
        "-u",
        help="Target website URL to crawl and analyze",
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        help="Optional login username/email for authenticated apps",
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        help="Optional login password (never logged or saved to disk)",
    ),
    max_steps: Optional[int] = typer.Option(
        None,
        "--max-steps",
        "-m",
        help="Safety cap on total AI exploration actions (defaults to DEFAULT_MAX_STEPS / 40)",
    ),
    headless: Optional[bool] = typer.Option(
        None,
        "--headless/--no-headless",
        help="Run browser in headless mode (defaults to HEADLESS / false)",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Directory to save the generated Postman collection (defaults to DEFAULT_OUTPUT_DIR / ./output)",
    ),
) -> None:
    """Execute autonomous crawling, network sniffing, AI mapping, and Postman export."""
    print_banner()

    # Validate target URL
    if not validate_target_url(url):
        console.print(
            f"[bold red]Error:[/bold red] Invalid target URL '{url}'. Must start with http:// or https:// and include a valid domain.",
            style="red",
        )
        raise typer.Exit(code=1)

    # Load configuration and resolve runtime defaults
    settings = get_settings()
    resolved_max_steps = max_steps if max_steps is not None else settings.default_max_steps
    resolved_headless = headless if headless is not None else settings.headless
    resolved_output_dir = output_dir if output_dir is not None else Path(settings.default_output_dir)

    # Display sanitized execution configuration
    display_configuration(
        url=url,
        username=username,
        password=password,
        max_steps=resolved_max_steps,
        headless=resolved_headless,
        output_dir=resolved_output_dir,
        model=settings.gemini_model,
    )

    # Ensure output directory exists
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    # Run asynchronous pipeline
    asyncio.run(
        run_pipeline(
            url=url,
            username=username,
            password=password,
            max_steps=resolved_max_steps,
            headless=resolved_headless,
            output_dir=resolved_output_dir,
        )
    )


def main() -> None:
    """Application entrypoint."""
    try:
        app()
    except Exception as exc:
        console.print(f"[bold red]Fatal Error:[/bold red] {exc}", style="red")
        sys.exit(1)


if __name__ == "__main__":
    main()
