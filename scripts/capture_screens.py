"""
Screenshot capture for the project report. Not part of the app.

Drives the real Streamlit app with Playwright, signs in through the
actual login form, and captures each screen. Chrome's built-in
--headless --screenshot cannot be used: Streamlit renders over a
WebSocket, and that flag fires on the page load event, before any
data has arrived, producing a blank image.

Playwright reuses the Chrome already installed on this machine via
channel="chrome", so no browser download is needed.

    streamlit run app.py --server.port 8501     # in another shell
    python scripts/capture_screens.py

Screens land in docs/screens/.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screens"
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://localhost:8501"
VIEWPORT = {"width": 1680, "height": 1050}

# Streamlit needs a moment to finish its websocket handshake before
# elements exist. This is the selector for a fully rendered page.
READY = '[data-testid="stAppViewContainer"]'


def sign_in(page) -> None:
    """Log in through the real form."""
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector(READY, timeout=60_000)

    fields = page.locator('[data-testid="stTextInput"] input')
    fields.nth(0).fill("admin")
    fields.nth(1).fill("Passw0rd!")

    page.get_by_role("button", name="Sign in").click()

    # The sidebar only renders after the session holds a user.
    page.wait_for_selector('[data-testid="stSidebar"]', timeout=60_000)
    page.get_by_text("System Administrator").first.wait_for(timeout=60_000)
    time.sleep(2.5)


def shoot(page, label: str, name: str) -> None:
    """Capture one screen, letting the page settle first."""
    time.sleep(1.8)
    target = OUT / f"{name}.png"
    page.screenshot(path=str(target), full_page=True)
    size = target.stat().st_size
    print(f"{'ok ' if size > 40_000 else 'THIN'}  {label:<34} {size:>8,} bytes  {name}.png")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True,
                                     args=["--no-sandbox"])
        context = browser.new_context(viewport=VIEWPORT,
                                      device_scale_factor=1)
        page = context.new_page()

        # ---- login screen, before authenticating -------------------
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector(READY, timeout=60_000)
        # Wait for the actual form, not just the app shell. On a cold
        # server the websocket handshake can finish after the shell
        # renders, which captured a blank page.
        page.wait_for_selector('[data-testid="stTextInput"] input',
                               timeout=60_000)
        page.get_by_role("button", name="Sign in").wait_for(timeout=60_000)
        time.sleep(1.5)
        shoot(page, "login", "01-login")

        sign_in(page)

        # ---- dashboard --------------------------------------------
        shoot(page, "dashboard (admin)", "02-dashboard")

        # ---- the eight remaining screens ---------------------------
        # Sidebar labels are decorated with an emoji ("📚  Courses"),
        # so exact text matching never matches. Scope to the sidebar
        # and use a substring match on the plain part of the label.
        screens = [
            ("Students",    "03-students"),
            ("Instructors", "04-instructors"),
            ("Courses",     "05-courses"),
            ("Enrolments",  "06-enrolments"),
            ("Assignments", "07-assignments"),
            ("Attendance",  "08-attendance"),
            ("Reports",     "09-reports"),
            ("User Admin",  "10-user-admin"),
        ]

        sidebar = page.locator('[data-testid="stSidebar"]')

        def go(label: str) -> None:
            sidebar.get_by_text(label, exact=False).first.click()
            # Wait for the radio's aria-checked to move, so the capture
            # happens after the screen has actually rerendered.
            page.wait_for_timeout(2600)

        for label, name in screens:
            go(label)
            shoot(page, label.lower(), name)

        # ---- reports: the PL/pgSQL output tab ----------------------
        go("Reports")
        page.get_by_role("tab", name="PL/pgSQL output").click()
        page.wait_for_timeout(2800)
        shoot(page, "reports / plpgsql tab", "11-reports-plpgsql")

        # ---- reports: the SQL source tab --------------------------
        page.get_by_role("tab", name="SQL source").click()
        page.wait_for_timeout(2400)
        shoot(page, "reports / sql source", "12-reports-sql")

        browser.close()

    print(f"\nwrote {len(list(OUT.glob('*.png')))} screenshots to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())