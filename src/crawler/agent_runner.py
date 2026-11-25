"""Autonomous browser crawler orchestrating tab traversal, functionality execution, and safety guardrails."""

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from rich.console import Console

from src.config import Settings, get_settings
from src.crawler.auth_handler import AuthHandler
from src.interceptor.context_tracker import ContextTracker
from src.interceptor.network_sniffer import NetworkSniffer
from src.models import CorrelatedEndpoint

console = Console()


@dataclass
class CrawlerResult:
    """Summary of autonomous crawl execution."""
    captured_endpoints: List[CorrelatedEndpoint] = field(default_factory=list)
    tabs_visited: List[str] = field(default_factory=list)
    actions_executed: int = 0
    destructive_actions_blocked: int = 0
    auth_success: Optional[bool] = None


class AgentRunner:
    """Orchestrates autonomous browser exploration, authentication, and API interception."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        context_tracker: Optional[ContextTracker] = None,
        sniffer: Optional[NetworkSniffer] = None,
    ):
        self.settings: Settings = settings or get_settings()
        self.context_tracker: ContextTracker = context_tracker or ContextTracker()
        self.sniffer: NetworkSniffer = sniffer or NetworkSniffer(
            context_tracker=self.context_tracker,
            settings=self.settings,
        )
        self.auth_handler = AuthHandler(settings=self.settings)

    async def crawl(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        max_steps: int = 40,
        headless: bool = False,
    ) -> CrawlerResult:
        """Execute autonomous exploration against target URL."""
        result = CrawlerResult()
        parsed_target = urlparse(target_url)
        target_domain = parsed_target.netloc.lower()

        console.print(f"[bold cyan]🚀 Launching browser crawler for:[/bold cyan] {target_url}")
        console.print(f"[dim]Mode: {'Headless' if headless else 'Headed (Live View)'} | Max Steps: {max_steps}[/dim]")

        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=headless)
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page: Page = await context.new_page()

            # Attach network sniffer to the page
            self.sniffer.attach(page)

            # Step 1: Initial Navigation
            await self.context_tracker.before_action("Initial Navigation", tab_name="General / Landing")
            try:
                await page.goto(target_url, wait_until="load", timeout=30000)
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as e:
                console.print(f"[yellow]Initial page load completed with warning: {e}[/yellow]")

            result.actions_executed += 1
            await self.context_tracker.after_action()

            # Step 2: Authentication (if credentials provided)
            if username and password:
                console.print("[bold yellow]🔑 Executing Authentication Phase...[/bold yellow]")
                await self.context_tracker.before_action("Authentication", tab_name="Auth")
                auth_ok = await self.auth_handler.authenticate(page, username=username, password=password)
                result.auth_success = auth_ok
                result.actions_executed += 1
                await self.context_tracker.after_action()
                if auth_ok:
                    try:
                        await page.wait_for_selector("nav a, [role='navigation'] a, .sidebar a, .nav-link", timeout=4000)
                    except Exception:
                        pass

            post_entry_url = page.url

            # Step 3: Discover Navigation Tabs
            tabs = await self._discover_tabs(page, target_domain)
            console.print(f"[bold cyan]📑 Discovered {len(tabs)} navigation tab(s):[/bold cyan] {', '.join(t['name'] for t in tabs) if tabs else 'None (single-page mode)'}")

            # Always include default landing tab
            result.tabs_visited.append("General / Landing")

            # Single-page mode: exercise interactive controls directly on the landing view
            if not tabs and result.actions_executed < max_steps:
                await self._exercise_tab_functionalities(
                    page=page,
                    tab_name="General / Landing",
                    result=result,
                    max_steps=max_steps,
                    target_domain=target_domain,
                )

            # Step 4: Iterate through Tabs and Exercise Safe Functionalities
            for tab_info in tabs:
                if result.actions_executed >= max_steps:
                    console.print(f"[yellow]⚠️ Max steps limit ({max_steps}) reached. Halting crawl.[/yellow]")
                    break

                tab_name = tab_info["name"]

                # Safety check on tab name itself (e.g. "Logout", "Delete Workspace")
                if self.settings.is_destructive_action(tab_name):
                    console.print(f"[bold red]🛡️ Safety Guardrail Triggered:[/bold red] Skipping destructive tab '[dim]{tab_name}[/dim]'")
                    result.destructive_actions_blocked += 1
                    continue

                console.print(f"\n[bold green]➡️ Exploring Tab:[/bold green] [bold]{tab_name}[/bold]")
                await self.context_tracker.before_action(f"Navigate to {tab_name}", tab_name=tab_name)

                # Navigate to the tab
                nav_success = await self._navigate_to_tab(
                    page=page,
                    tab_info=tab_info,
                    target_domain=target_domain,
                    fallback_base_url=post_entry_url,
                )
                if not nav_success:
                    console.print(f"[yellow]⚠️ Could not navigate to tab '{tab_name}'. Skipping tab controls.[/yellow]")
                    await self.context_tracker.after_action()
                    continue

                result.tabs_visited.append(tab_name)
                result.actions_executed += 1
                await asyncio.sleep(0.5)
                await self.context_tracker.after_action()

                # Exercise non-destructive interactive controls on this tab
                await self._exercise_tab_functionalities(
                    page=page,
                    tab_name=tab_name,
                    result=result,
                    max_steps=max_steps,
                    target_domain=target_domain,
                )

            # Flush any in-flight response body captures before closing browser
            await self.sniffer.wait_for_pending(timeout=2.0)

            # Exploration finished
            await browser.close()

        result.captured_endpoints = self.sniffer.captured_endpoints
        console.print(f"\n[bold green]✓ Crawl Completed:[/bold green] Visited {len(result.tabs_visited)} tabs, executed {result.actions_executed} actions, blocked {result.destructive_actions_blocked} destructive actions, intercepted {len(result.captured_endpoints)} API endpoints.")
        return result

    async def _discover_tabs(self, page: Page, target_domain: str) -> List[dict]:
        """Identify top-level navigation tabs, sidebars, and menu links."""
        tab_candidates: List[dict] = []
        seen_names: Set[str] = set()

        tab_selectors = [
            'nav a',
            '[role="navigation"] a',
            '[role="tab"]',
            '.nav-link',
            '.menu-item a',
            '.sidebar a',
            'header a',
        ]

        for selector in tab_selectors:
            locators = page.locator(selector)
            count = await locators.count()
            for i in range(min(count, 20)):
                loc = locators.nth(i)
                try:
                    if not await loc.is_visible():
                        continue
                    text = (await loc.text_content() or "").strip()
                    if not text or len(text) > 40:
                        continue
                    href = (await loc.get_attribute("href") or "").strip()
                    resolved_href = urljoin(page.url, href) if href and not href.startswith(("#", "javascript:")) else ""

                    # Ensure on-domain if HTTP(S) link
                    if resolved_href.startswith(("http://", "https://")):
                        parsed_href = urlparse(resolved_href)
                        if parsed_href.netloc.lower() != target_domain:
                            continue

                    norm_name = text.lower()
                    if norm_name not in seen_names:
                        seen_names.add(norm_name)
                        tab_candidates.append({
                            "name": text,
                            "selector": selector,
                            "index": i,
                            "href": href,
                            "resolved_href": resolved_href,
                        })
                except Exception:
                    continue

        return tab_candidates

    async def _find_tab_locator(self, page: Page, tab_info: dict):
        """Resolve a tab locator dynamically by href or text to avoid stale DOM indices."""
        selector = tab_info["selector"]
        tab_name = tab_info["name"]
        href = tab_info.get("href") or ""

        locators = page.locator(selector)
        count = await locators.count()
        for i in range(min(count, 30)):
            candidate = locators.nth(i)
            try:
                if not await candidate.is_visible():
                    continue
                cand_text = (await candidate.text_content() or "").strip()
                cand_href = (await candidate.get_attribute("href") or "").strip()
                if href and cand_href == href:
                    return candidate
                if cand_text == tab_name:
                    return candidate
            except Exception:
                continue

        # Final fallback to original index only if its visible text still matches
        fallback = locators.nth(tab_info["index"])
        try:
            if await fallback.is_visible():
                fb_text = (await fallback.text_content() or "").strip()
                if fb_text == tab_name:
                    return fallback
        except Exception:
            pass
        return None

    async def _navigate_to_tab(
        self,
        page: Page,
        tab_info: dict,
        target_domain: str = "",
        fallback_base_url: str = "",
    ) -> bool:
        """Click on or navigate to a discovered tab, recovering if DOM shifted."""
        loc = await self._find_tab_locator(page, tab_info)
        if loc is None and fallback_base_url and page.url != fallback_base_url:
            try:
                await page.goto(fallback_base_url, wait_until="load", timeout=8000)
                loc = await self._find_tab_locator(page, tab_info)
            except Exception:
                loc = None

        if loc is not None:
            try:
                await loc.click(timeout=4000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                return True
            except Exception:
                pass

        # Fallback to resolved href navigation if available and on-domain
        resolved_href = tab_info.get("resolved_href") or ""
        if resolved_href.startswith(("http://", "https://")):
            if not target_domain or urlparse(resolved_href).netloc.lower() == target_domain:
                try:
                    await page.goto(resolved_href, wait_until="load", timeout=8000)
                    return True
                except Exception:
                    pass
        return False

    async def _exercise_tab_functionalities(
        self,
        page: Page,
        tab_name: str,
        result: CrawlerResult,
        max_steps: int,
        target_domain: str = "",
    ) -> None:
        """Trigger non-destructive UI features (search, filters, sorting, pagination, safe view buttons)."""
        # 1. Search Inputs: Enter safe test query
        if result.actions_executed < max_steps:
            search_inputs = page.locator('input[type="search"], input[placeholder*="search" i], input[name*="search" i]')
            if await search_inputs.count() > 0 and await search_inputs.first.is_visible():
                input_loc = search_inputs.first
                placeholder = await input_loc.get_attribute("placeholder") or "search"
                action_name = f"Search Filter: '{placeholder}'"
                console.print(f"[cyan]  ⚡ Triggering UI Action:[/cyan] {action_name}")
                await self.context_tracker.before_action(action_name, tab_name=tab_name)
                try:
                    await input_loc.fill("test")
                    await input_loc.press("Enter")
                    await asyncio.sleep(0.4)
                except Exception:
                    pass
                result.actions_executed += 1
                await self.context_tracker.after_action()

        # 2. Interactive Buttons: Discover all buttons & controls, filtering through safety guardrails
        button_selectors = [
            'button',
            'a.btn',
            'a.button',
            'th.sortable',
            '[aria-label*="next" i]',
            '.page-link',
        ]

        for sel in button_selectors:
            if result.actions_executed >= max_steps:
                break
            buttons = page.locator(sel)
            count = await buttons.count()
            for idx in range(min(count, 5)):
                if result.actions_executed >= max_steps:
                    break
                btn = buttons.nth(idx)
                try:
                    if not await btn.is_visible():
                        continue

                    # Prevent clicking anchor buttons that navigate off-domain or open popups
                    href = (await btn.get_attribute("href") or "").strip()
                    target_attr = (await btn.get_attribute("target") or "").strip().lower()
                    if target_attr == "_blank":
                        continue
                    if href and not href.startswith(("#", "javascript:")) and target_domain:
                        resolved = urljoin(page.url, href)
                        if resolved.startswith(("http://", "https://")) and urlparse(resolved).netloc.lower() != target_domain:
                            continue

                    text_val = (await btn.text_content() or "").strip()
                    aria_val = (await btn.get_attribute("aria-label") or "").strip()
                    title_val = (await btn.get_attribute("title") or "").strip()
                    btn_text = (text_val or aria_val or title_val or sel).strip()
                    if not btn_text:
                        continue

                    # Guardrail: Check destructive blocklist across visible text and accessibility labels
                    guardrail_text = " ".join(p for p in (text_val, aria_val, title_val) if p)
                    if self.settings.is_destructive_action(guardrail_text):
                        console.print(f"[bold red]  🛡️ Blocked Destructive Action:[/bold red] '{btn_text}'")
                        result.destructive_actions_blocked += 1
                        continue

                    action_name = f"Click {btn_text[:30]}"
                    console.print(f"[cyan]  ⚡ Triggering UI Action:[/cyan] {action_name}")
                    await self.context_tracker.before_action(action_name, tab_name=tab_name)
                    await btn.click(timeout=3000)
                    await asyncio.sleep(0.4)
                    result.actions_executed += 1
                    await self.context_tracker.after_action()
                except Exception:
                    continue
