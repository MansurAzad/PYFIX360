"""Browser automation for the app-builder platforms.

Some of these platforms publish no API. The only way to operate them is a
signed-in web session, which means driving a real browser. That is
inherently fragile: the selectors below are our best guess at the current
markup, and they break the day the vendor redesigns a page. The API path
(:mod:`pyfix360.integrations.api_clients`) is the stable one; this is the
fallback.

Rules that keep this honest:

* **Playwright is optional.** It lives in the ``[automation]`` extra. Without
  it this module still imports and every operation raises
  :class:`AutomationUnavailable` with the exact install line.
* **We never log in.** The user signs in themselves, in a real browser
  profile, and this module attaches to that session. No credentials are
  handled here at all.
* **Selectors are data, not code.** They live in :data:`SELECTORS` and can be
  overridden per platform at runtime, so a broken selector is a
  configuration change rather than a code change.
* **Failures leave evidence.** A screenshot is written to the log directory
  when an operation fails, so a broken selector can actually be diagnosed.
* **Nothing runs implicitly.** A session is opened explicitly by the caller,
  and the caller must have passed the access-control gate first.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from pyfix360.integrations.api_clients import CallResult


class AutomationError(Exception):
    """Base for browser automation failures."""


class AutomationUnavailable(AutomationError):
    """Playwright is not installed, or its browser binaries are missing."""


class AutomationFailed(AutomationError):
    """The page was reached but the expected element was not there."""


@dataclass(frozen=True)
class SelectorSet:
    """Candidate selectors for one platform, tried in order."""

    prompt_input: tuple[str, ...]
    send_button: tuple[str, ...]
    response: tuple[str, ...]
    ready_marker: tuple[str, ...] = ()


#: Where each platform lives, and how its prompt box is likely to be found.
#: Placeholder and role selectors are preferred over CSS classes because they
#: survive a restyle far more often.
SELECTORS: dict[str, SelectorSet] = {
    "lovable": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='Ask Lovable' i]",
            "textarea[placeholder*='describe' i]",
            "textarea[placeholder*='prompt' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-testid*='message' i]",
            "[data-message-author-role]",
            "article",
            ".prose",
        ),
    ),
    "ai-studio": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='Enter a prompt' i]",
            "textarea[placeholder*='prompt' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-test-id*='response' i]",
            "model-response",
            "ms-model-response",
            "div[role=region]",
        ),
    ),
    "v0": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='Ask' i]",
            "textarea[placeholder*='Describe' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-testid*='message' i]",
            "pre code",
            "article",
        ),
    ),
    "replit": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='Ask Replit' i]",
            "textarea[placeholder*='Ask' i]",
            "textarea[placeholder*='message' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-testid*='message' i]",
            ".conversation-item",
            "article",
        ),
    ),
    "trickle": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='Describe' i]",
            "textarea[placeholder*='prompt' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-testid*='message' i]",
            "article",
            ".prose",
        ),
    ),
    "emergent": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='Build' i]",
            "textarea[placeholder*='Describe' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-testid*='message' i]",
            "article",
            ".prose",
        ),
    ),
    "builder": SelectorSet(
        prompt_input=(
            "textarea[placeholder*='prompt' i]",
            "textarea[placeholder*='Ask' i]",
            "textarea",
        ),
        send_button=(
            "button[aria-label*='Send' i]",
            "button[type=submit]",
        ),
        response=(
            "[data-testid*='message' i]",
            "article",
            ".prose",
        ),
    ),
}

#: Where each platform's web app lives.
LANDING_URLS: dict[str, str] = {
    "lovable": "https://lovable.dev/",
    "ai-studio": "https://aistudio.google.com/prompts/new_chat",
    "v0": "https://v0.app/chat",
    "replit": "https://replit.com/",
    "trickle": "https://trickle.so/",
    "emergent": "https://app.emergent.sh/",
    "builder": "https://www.builder.io/",
}

#: Platforms whose prompt can be prefilled through the URL, which is far more
#: stable than typing into the box.
DEEP_LINKS: dict[str, str] = {
    "lovable": "https://lovable.dev/#prompt={prompt}",
    "ai-studio": "https://aistudio.google.com/prompts/new_chat?prompt={prompt}",
}

INSTALL_HINT = (
    "Browser automation needs Playwright:\n"
    "    pip install -e \".[automation]\"\n"
    "    python -m playwright install chromium"
)


def playwright_available() -> tuple[bool, str]:
    """``(available, reason)`` — why not, when it is not available."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, "the playwright package is not installed"
    return True, ""


@dataclass
class SelectorOverride:
    """A caller-supplied replacement for the built-in selectors."""

    platform: str
    prompt_input: tuple[str, ...] = ()
    send_button: tuple[str, ...] = ()
    response: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)

    def merged(self, base: SelectorSet) -> SelectorSet:
        return SelectorSet(
            prompt_input=self.prompt_input or base.prompt_input,
            send_button=self.send_button or base.send_button,
            response=self.response or base.response,
            ready_marker=base.ready_marker,
        )


class BrowserSession:
    """A browser session that can drive one platform's chat.

    Use it as a context manager::

        with BrowserSession(headless=False) as session:
            result = session.submit_prompt("v0", "Build a todo app")
    """

    def __init__(self, *, headless: bool = True, timeout_s: int = 60,
                 selectors: dict[str, SelectorOverride] | None = None,
                 browser_channel: str = "", user_data_dir: str = "",
                 slow_mo_ms: int = 0) -> None:
        self.headless = headless
        self.timeout_s = timeout_s
        self.browser_channel = browser_channel
        self.user_data_dir = user_data_dir
        self.slow_mo_ms = slow_mo_ms
        self._overrides = selectors or {}
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> BrowserSession:
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self) -> None:
        available, reason = playwright_available()
        if not available:
            raise AutomationUnavailable(f"{reason}.\n{INSTALL_HINT}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - guarded above
            raise AutomationUnavailable(f"{exc}.\n{INSTALL_HINT}") from exc

        try:
            self._playwright = sync_playwright().start()
            launch: dict = {"headless": self.headless}
            if self.browser_channel:
                launch["channel"] = self.browser_channel
            if self.user_data_dir:
                launch["user_data_dir"] = self.user_data_dir
            if self.slow_mo_ms:
                launch["slow_mo"] = self.slow_mo_ms
            self._browser = self._playwright.chromium.launch(**launch)
            self._context = self._browser.new_context()
            self._context.set_default_timeout(self.timeout_s * 1000)
            self._page = self._context.new_page()
        except Exception as exc:
            self.close()
            raise AutomationUnavailable(
                f"the browser could not be started: {exc}\n{INSTALL_HINT}"
            ) from exc

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    # -- helpers ----------------------------------------------------------
    @property
    def page(self):
        if self._page is None:
            raise AutomationFailed("the browser session is not open")
        return self._page

    def selectors_for(self, platform_key: str) -> SelectorSet:
        base = SELECTORS.get(platform_key)
        if base is None:
            raise AutomationFailed(
                f"no browser strategy is defined for '{platform_key}'.")
        override = self._overrides.get(platform_key)
        return override.merged(base) if override else base

    def _first_visible(self, selectors: tuple[str, ...]):
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    def _screenshot(self, label: str) -> str:
        """Save evidence of a failure so a broken selector can be diagnosed."""
        try:
            from pyfix360.config import get_config

            target = Path(get_config().logs_dir) / f"automation-{label}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(target))
            return str(target)
        except Exception:
            return ""

    def _enter_prompt(self, text: str, selectors: SelectorSet) -> None:
        box = self._first_visible(selectors.prompt_input)
        if box is None:
            shot = self._screenshot("no-input")
            raise AutomationFailed(
                "the prompt box was not found. The page layout has probably "
                "changed — override the selectors for this platform.\n"
                f"screenshot: {shot}")
        box.click()
        box.fill(text)
        button = self._first_visible(selectors.send_button)
        if button is not None:
            button.click()
        else:
            self.page.keyboard.press("Enter")

    # -- operations -------------------------------------------------------
    def open_platform(self, platform_key: str) -> str:
        """Navigate to a platform and return the URL it landed on."""
        url = LANDING_URLS.get(platform_key)
        if url is None:
            raise AutomationFailed(
                f"no landing page is known for '{platform_key}'.")
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page.url

    def submit_prompt(self, platform_key: str, prompt: str) -> CallResult:
        """Submit a build prompt and return the page it was submitted on."""
        if not prompt.strip():
            raise AutomationFailed("the prompt is empty.")
        selectors = self.selectors_for(platform_key)

        deep_link = DEEP_LINKS.get(platform_key)
        if deep_link:
            url = deep_link.format(prompt=urllib.parse.quote(prompt, safe=""))
            self.page.goto(url, wait_until="domcontentloaded")
            # The prompt is already in the box; only the submit is left.
            button = self._first_visible(selectors.send_button)
            if button is not None:
                button.click()
            else:
                box = self._first_visible(selectors.prompt_input)
                if box is None:
                    raise AutomationFailed(
                        "the prefilled prompt could not be confirmed.")
                box.press("Enter")
        else:
            self.open_platform(platform_key)
            self._enter_prompt(prompt, selectors)

        return CallResult(handle=self.page.url, status="submitted",
                          output="", raw={"url": self.page.url})

    def send_followup(self, platform_key: str, text: str) -> CallResult:
        """Answer a question the platform asked, in the same chat."""
        if not text.strip():
            raise AutomationFailed("the message is empty.")
        selectors = self.selectors_for(platform_key)
        self._enter_prompt(text, selectors)
        return CallResult(handle=self.page.url, status="submitted", raw={})

    def read_last_response(self, platform_key: str) -> str:
        """Read the last message the platform produced."""
        selectors = self.selectors_for(platform_key)
        node = self._first_visible(selectors.response)
        if node is None:
            shot = self._screenshot("no-response")
            return (f"(no response element found — screenshot: {shot})")
        try:
            return node.inner_text().strip()
        except Exception:
            return ""

    def probe(self, platform_key: str) -> dict[str, bool]:
        """Report which selectors currently match. Used to diagnose breakage."""
        selectors = self.selectors_for(platform_key)
        self.open_platform(platform_key)
        return {
            "prompt_input": self._first_visible(selectors.prompt_input) is not None,
            "send_button": self._first_visible(selectors.send_button) is not None,
            "response": self._first_visible(selectors.response) is not None,
        }
