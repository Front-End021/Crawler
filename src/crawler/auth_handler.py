"""Authentication handler for detecting login forms and authenticating web applications."""

from typing import Optional, Tuple
from playwright.async_api import Page
from rich.console import Console

from src.config import Settings, get_settings

console = Console()


class AuthHandler:
    """Manages autonomous authentication without leaking credentials to logs or disk."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings: Settings = settings or get_settings()

    async def find_login_elements(self, page: Page) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Identify selectors for username/email input, password input, and submit button."""
        # 1. Search for username/email input selector
        username_selectors = [
            'input[type="email"]',
            'input[name*="user" i]',
            'input[name*="email" i]',
            'input[name*="login" i]',
            'input[id*="user" i]',
            'input[id*="email" i]',
            'input[placeholder*="user" i]',
            'input[placeholder*="email" i]',
            'input[placeholder*="username" i]',
            'input[type="text"]',
        ]
        user_sel = None
        for sel in username_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                user_sel = sel
                break

        # 2. Search for password input selector
        password_selectors = [
            'input[type="password"]',
            'input[name*="pass" i]',
            'input[id*="pass" i]',
            'input[placeholder*="password" i]',
        ]
        pass_sel = None
        for sel in password_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                pass_sel = sel
                break

        # 3. Search for submit button
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            'button:has-text("Submit")',
        ]
        submit_sel = None
        for sel in submit_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                submit_sel = sel
                break

        return user_sel, pass_sel, submit_sel

    async def authenticate(
        self,
        page: Page,
        username: str,
        password: str,
        timeout_ms: int = 8000,
    ) -> bool:
        """Locate login elements, submit credentials, and verify authenticated session."""
        console.print("[cyan]🔍 Detecting login form fields...[/cyan]")

        initial_url = page.url
        user_sel, pass_sel, submit_sel = await self.find_login_elements(page)

        # If not directly visible on initial page, check for a "Sign In" or "Login" link/button
        if not (user_sel and pass_sel):
            login_link_selectors = [
                'a:has-text("Login")',
                'a:has-text("Sign In")',
                'a:has-text("Log in")',
                'a[href*="login" i]',
                'a[href*="signin" i]',
                'button:has-text("Login")',
                'button:has-text("Sign In")',
            ]
            for link_sel in login_link_selectors:
                loc = page.locator(link_sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    console.print(f"[dim]Navigating to login view via '{link_sel}'...[/dim]")
                    await loc.click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        pass
                    user_sel, pass_sel, submit_sel = await self.find_login_elements(page)
                    if user_sel and pass_sel:
                        break

        if not (user_sel and pass_sel):
            console.print("[yellow]⚠️ Could not locate login form inputs on target page.[/yellow]")
            return False

        # Fill username
        console.print(f"[cyan]Filling username for: [bold]{username}[/bold][/cyan]")
        await page.fill(user_sel, username)

        # Fill password securely (never print value)
        console.print("[cyan]Filling password: [dim]********[/dim][/cyan]")
        await page.fill(pass_sel, password)

        # Submit
        if submit_sel:
            await page.click(submit_sel)
        else:
            await page.press(pass_sel, "Enter")

        # Wait for navigation or state change (handles slow SPA redirects after form submit)
        try:
            await page.wait_for_url(lambda u: u != initial_url, timeout=min(timeout_ms, 5000))
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

        # Check authentication indicators
        current_url = page.url
        url_changed = current_url != initial_url and "login" not in current_url.lower()

        # Check if login form disappeared or dashboard indicator appeared
        login_still_visible = False
        if pass_sel:
            try:
                login_still_visible = await page.locator(pass_sel).first.is_visible()
            except Exception:
                login_still_visible = False

        authenticated = url_changed or not login_still_visible

        if authenticated:
            console.print("[bold green]✓ Authentication verified successfully.[/bold green]")
        else:
            console.print("[yellow]⚠️ Warning: Login submitted but authenticated dashboard state was not confirmed.[/yellow]")

        return authenticated
