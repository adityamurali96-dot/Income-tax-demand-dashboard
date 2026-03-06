"""
Income Tax Portal Scraper — Playwright-based automation.

Logs into incometax.gov.in, navigates the portal, and extracts:
1. e-Proceedings (notices, scrutiny, etc.)
2. Outstanding Tax Demands

Uses headed browser mode with deliberate delays to avoid detection.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

PORTAL_BASE = "https://eportal.incometax.gov.in"
LOGIN_URL = f"{PORTAL_BASE}/iec/foservices/#/login"
EPROCEEDINGS_URL = f"{PORTAL_BASE}/iec/foservices/#/dashboard/eProceedings"
DEMANDS_URL = f"{PORTAL_BASE}/iec/foservices/#/dashboard/outstandingDemand"

COOKIE_DIR = Path("session_cookies")
COOKIE_DIR.mkdir(exist_ok=True)

# Throttle between actions (seconds)
ACTION_DELAY = 2.5

# Timeout for waiting on Angular-rendered elements (ms)
ELEMENT_WAIT_TIMEOUT = 60000
# Timeout for page navigation (ms)
NAV_TIMEOUT = 60000


async def _delay(seconds: float = ACTION_DELAY):
    await asyncio.sleep(seconds)


async def _wait_for_any_selector(page, selectors: list[str], timeout: int = ELEMENT_WAIT_TIMEOUT):
    """
    Wait for any one of the given selectors to become visible.
    Returns the first matching locator, or None if none found.
    This is essential for Angular SPAs where element IDs may vary
    between portal deployments.
    """
    combined = ", ".join(selectors)
    try:
        await page.wait_for_selector(combined, state="visible", timeout=timeout)
    except Exception:
        pass

    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def _click_first_enabled_selector(
    page,
    selectors: list[str],
    timeout: int = ELEMENT_WAIT_TIMEOUT,
    retries: int = 10,
):
    """Click the first matching selector once it becomes enabled.

    On the IT portal, Angular often renders the button quickly but keeps it
    disabled until form validation and blur events complete. This helper waits
    for visibility and retries until a control is actually enabled.
    """
    btn = await _wait_for_any_selector(page, selectors, timeout=timeout)
    if not btn:
        return False

    for _ in range(retries):
        try:
            if await btn.is_enabled():
                await btn.click()
                return True
        except Exception:
            pass
        await _delay(0.5)

    return False


class PortalScraper:
    """Manages a Playwright browser session against the IT portal."""

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._logged_in = False
        self._pan: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def launch(self):
        """Start the browser in headed mode with a virtual display.

        The Income Tax portal detects and blocks headless browsers.
        We run Chromium in headed mode against an Xvfb virtual framebuffer
        so it behaves like a real desktop browser, even on a headless server.
        """
        # Start Xvfb virtual display if no display is available
        self._ensure_virtual_display()

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        # Remove the webdriver flag that sites use to detect automation
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        self._page = await self._context.new_page()
        logger.info("Browser launched (headed mode with virtual display)")

    def _ensure_virtual_display(self):
        """Start Xvfb if DISPLAY is not set (headless server environment)."""
        if os.environ.get("DISPLAY"):
            logger.info("DISPLAY already set to %s", os.environ["DISPLAY"])
            return

        display_num = ":99"
        try:
            # Check if Xvfb is already running on :99
            result = subprocess.run(
                ["xdpyinfo", "-display", display_num],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                os.environ["DISPLAY"] = display_num
                logger.info("Reusing existing Xvfb on %s", display_num)
                return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            subprocess.Popen(
                ["Xvfb", display_num, "-screen", "0", "1280x800x24", "-ac"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.environ["DISPLAY"] = display_num
            # Give Xvfb a moment to start
            time.sleep(1)
            logger.info("Started Xvfb on %s", display_num)
        except FileNotFoundError:
            logger.warning(
                "Xvfb not found — falling back to headless mode. "
                "Install xvfb for headed browser support: apt-get install xvfb"
            )

    async def close(self):
        """Shut down browser and Playwright."""
        if self._context:
            # Save cookies before closing
            if self._pan:
                cookies = await self._context.cookies()
                cookie_file = COOKIE_DIR / f"{self._pan}.json"
                cookie_file.write_text(json.dumps(cookies))
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._logged_in = False
        logger.info("Browser closed")

    # ------------------------------------------------------------------
    # Login flow (Step 1: credentials → Step 2: OTP)
    # ------------------------------------------------------------------

    async def start_login(self, pan: str, password: str) -> dict:
        """
        Navigate to login page, enter PAN + password, and submit.
        Returns a status dict indicating whether OTP is needed.

        The Income Tax portal is an Angular SPA that renders dynamically.
        We use domcontentloaded (not networkidle) for navigation and then
        explicitly wait for Angular-rendered elements to appear.
        """
        self._pan = pan.upper().strip()

        if not self._page:
            await self.launch()

        page = self._page

        try:
            # --- Step 1: Navigate to the portal login page ---
            # Use domcontentloaded instead of networkidle because Angular
            # SPAs keep making XHR calls that prevent networkidle from
            # ever resolving, causing the timeout.
            logger.info("Navigating to login page: %s", LOGIN_URL)
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await _delay(3)

            # --- Step 2: Wait for Angular to render the PAN input ---
            # The portal may use different selectors across deployments,
            # so we try multiple known patterns.
            PAN_SELECTORS = [
                'input[id="panAdhaarUserId"]',
                'input[formcontrolname="panAdhaarUserId"]',
                'input[placeholder*="User ID"]',
                'input[placeholder*="PAN"]',
                'input[name*="pan"]',
                'input[name*="userId"]',
            ]
            logger.info("Waiting for PAN input field to render...")
            pan_input = await _wait_for_any_selector(page, PAN_SELECTORS)

            if not pan_input:
                # Last-resort fallback: first visible text input on the page
                logger.warning("Primary PAN selectors not found, using fallback")
                fallback = page.locator('input[type="text"]').first
                try:
                    await fallback.wait_for(state="visible", timeout=30000)
                    pan_input = fallback
                except Exception:
                    return {
                        "status": "error",
                        "message": (
                            "Login failed: Could not find the PAN/User ID input field. "
                            "The Income Tax portal may be down or its UI has changed."
                        ),
                    }

            # --- Step 3: Enter PAN ---
            await pan_input.click()
            await pan_input.fill("")
            await pan_input.type(self._pan, delay=50)
            # Trigger blur to allow Angular form validators to enable Continue.
            await pan_input.press("Tab")
            await _delay(1)
            logger.info("PAN entered")

            # --- Step 4: Click Continue after PAN entry ---
            CONTINUE_SELECTORS = [
                'button:has-text("Continue")',
                'button:has-text("CONTINUE")',
                'button[type="submit"]',
                'input[type="submit"]',
            ]
            clicked_continue = await _click_first_enabled_selector(
                page,
                CONTINUE_SELECTORS,
                timeout=10000,
                retries=20,
            )
            if clicked_continue:
                await _delay(3)
                logger.info("Clicked Continue after PAN")
            else:
                return {
                    "status": "error",
                    "message": "Login failed: Continue button stayed disabled after entering PAN.",
                }

            # --- Step 5: Wait for password field (Angular re-renders the form) ---
            PASSWORD_SELECTORS = [
                'input[type="password"]',
                'input[formcontrolname="password"]',
                'input[placeholder*="Password"]',
                'input[placeholder*="password"]',
            ]
            pwd_input = await _wait_for_any_selector(page, PASSWORD_SELECTORS, timeout=30000)
            if not pwd_input:
                # Check if there was a validation error on PAN
                error_el = page.locator(
                    '.error-message, .alert-danger, [class*="error"], .mat-error'
                ).first
                try:
                    if await error_el.is_visible():
                        error_text = await error_el.text_content()
                        return {"status": "error", "message": f"PAN validation error: {error_text.strip()}"}
                except Exception:
                    pass
                return {
                    "status": "error",
                    "message": "Login failed: Password field did not appear. PAN may be invalid.",
                }

            # --- Step 6: Enter password ---
            await pwd_input.click()
            await pwd_input.fill("")
            await pwd_input.type(password, delay=30)
            await pwd_input.press("Tab")
            await _delay(1)
            logger.info("Password entered")

            # --- Step 7: Click Continue/Login to submit credentials ---
            LOGIN_BTN_SELECTORS = [
                'button:has-text("Continue")',
                'button:has-text("CONTINUE")',
                'button:has-text("Login")',
                'button:has-text("LOGIN")',
                'button[type="submit"]',
            ]
            clicked_login = await _click_first_enabled_selector(
                page,
                LOGIN_BTN_SELECTORS,
                timeout=10000,
                retries=20,
            )
            if clicked_login:
                await _delay(4)
                logger.info("Clicked Login/Continue")
            else:
                return {
                    "status": "error",
                    "message": "Login failed: Login/Continue button stayed disabled after entering password.",
                }

            # --- Step 8: Check for error messages ---
            error_el = page.locator(
                '.error-message, .alert-danger, [class*="error"], .mat-error, .toast-error'
            ).first
            try:
                if await error_el.is_visible():
                    error_text = await error_el.text_content()
                    if error_text and error_text.strip():
                        return {"status": "error", "message": error_text.strip()}
            except Exception:
                pass

            # --- Step 9: Check if OTP input appeared ---
            OTP_SELECTORS = [
                'input[id*="otp"]',
                'input[placeholder*="OTP"]',
                'input[name*="otp"]',
                'input[formcontrolname*="otp"]',
                'input[placeholder*="verification"]',
            ]
            otp_input = await _wait_for_any_selector(page, OTP_SELECTORS, timeout=15000)
            if otp_input:
                return {"status": "otp_required", "message": "OTP sent to your registered mobile/email."}

            # --- Step 10: Check if already on dashboard ---
            if "dashboard" in page.url.lower():
                self._logged_in = True
                return {"status": "success", "message": "Logged in successfully."}

            return {"status": "otp_required", "message": "Please enter the OTP sent to your registered mobile/email."}

        except Exception as e:
            logger.exception("Login failed")
            return {"status": "error", "message": f"Login failed: {str(e)}"}

    async def submit_otp(self, otp: str) -> dict:
        """Enter OTP and complete login."""
        if not self._page:
            return {"status": "error", "message": "Browser session not found. Please login again."}

        page = self._page

        try:
            # Find OTP input using multiple selector strategies
            OTP_SELECTORS = [
                'input[id*="otp"]',
                'input[placeholder*="OTP"]',
                'input[name*="otp"]',
                'input[formcontrolname*="otp"]',
                'input[placeholder*="verification"]',
            ]
            otp_input = await _wait_for_any_selector(page, OTP_SELECTORS, timeout=15000)
            if not otp_input:
                # Fallback to first visible text input
                otp_input = page.locator('input[type="text"]').first
                try:
                    await otp_input.wait_for(state="visible", timeout=10000)
                except Exception:
                    return {"status": "error", "message": "Could not find OTP input field."}

            await otp_input.click()
            await otp_input.fill("")
            await otp_input.type(otp.strip(), delay=50)
            await _delay(1)

            # Submit OTP
            SUBMIT_SELECTORS = [
                'button:has-text("Continue")',
                'button:has-text("CONTINUE")',
                'button:has-text("Validate")',
                'button:has-text("Submit")',
                'button:has-text("SUBMIT")',
                'button[type="submit"]',
            ]
            submit_btn = await _wait_for_any_selector(page, SUBMIT_SELECTORS, timeout=10000)
            if submit_btn:
                await submit_btn.click()
                await _delay(5)

            # Check for errors
            error_el = page.locator(
                '.error-message, .alert-danger, [class*="error"], .mat-error, .toast-error'
            ).first
            try:
                if await error_el.is_visible():
                    error_text = await error_el.text_content()
                    if error_text and error_text.strip():
                        return {"status": "error", "message": error_text.strip()}
            except Exception:
                pass

            # Check if dashboard loaded
            try:
                await page.wait_for_url("**/dashboard**", timeout=20000)
                self._logged_in = True
                return {"status": "success", "message": "Logged in successfully."}
            except Exception:
                pass

            # Fallback: check if URL changed from login
            if "login" not in page.url.lower():
                self._logged_in = True
                return {"status": "success", "message": "Logged in successfully."}

            return {"status": "error", "message": "OTP verification may have failed. Please try again."}

        except Exception as e:
            logger.exception("OTP submission failed")
            return {"status": "error", "message": f"OTP submission failed: {str(e)}"}

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    async def scrape_eproceedings(self) -> list[dict]:
        """Navigate to e-Proceedings and extract the table data."""
        if not self._logged_in or not self._page:
            return []

        page = self._page
        results = []

        try:
            await page.goto(EPROCEEDINGS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await _delay(3)

            # Wait for the proceedings table to load
            table = page.locator("table, .mat-table, [class*='proceeding']").first
            try:
                await table.wait_for(timeout=15000)
            except Exception:
                logger.warning("e-Proceedings table not found, trying alternate approach")
                # Try clicking into the e-Proceedings menu item
                menu = page.locator('a:has-text("e-Proceedings"), span:has-text("e-Proceedings")').first
                if await menu.is_visible():
                    await menu.click()
                    await _delay(3)

            # Extract rows from the table
            rows = await page.locator("table tbody tr, .mat-row").all()
            for row in rows:
                cells = await row.locator("td, .mat-cell").all()
                if len(cells) >= 4:
                    cell_texts = []
                    for cell in cells:
                        text = await cell.text_content()
                        cell_texts.append(text.strip() if text else "")

                    record = {
                        "assessment_year": cell_texts[0] if len(cell_texts) > 0 else "",
                        "notice_type": cell_texts[1] if len(cell_texts) > 1 else "",
                        "section": cell_texts[2] if len(cell_texts) > 2 else "",
                        "date_of_issue": cell_texts[3] if len(cell_texts) > 3 else "",
                        "response_due_date": cell_texts[4] if len(cell_texts) > 4 else "",
                        "status": cell_texts[5] if len(cell_texts) > 5 else "pending",
                        "portal_ref_id": cell_texts[6] if len(cell_texts) > 6 else "",
                    }
                    results.append(record)

            logger.info(f"Scraped {len(results)} e-Proceedings records")

        except Exception as e:
            logger.exception("Failed to scrape e-Proceedings")

        return results

    async def scrape_demands(self) -> list[dict]:
        """Navigate to Outstanding Demands and extract the table data."""
        if not self._logged_in or not self._page:
            return []

        page = self._page
        results = []

        try:
            await page.goto(DEMANDS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await _delay(3)

            # Wait for demands table
            table = page.locator("table, .mat-table, [class*='demand']").first
            try:
                await table.wait_for(timeout=15000)
            except Exception:
                logger.warning("Demands table not found, trying alternate approach")
                menu = page.locator('a:has-text("Outstanding Demand"), span:has-text("Outstanding Demand")').first
                if await menu.is_visible():
                    await menu.click()
                    await _delay(3)

            # Extract rows
            rows = await page.locator("table tbody tr, .mat-row").all()
            for row in rows:
                cells = await row.locator("td, .mat-cell").all()
                if len(cells) >= 3:
                    cell_texts = []
                    for cell in cells:
                        text = await cell.text_content()
                        cell_texts.append(text.strip() if text else "")

                    record = {
                        "assessment_year": cell_texts[0] if len(cell_texts) > 0 else "",
                        "section": cell_texts[1] if len(cell_texts) > 1 else "",
                        "demand_amount": _parse_amount(cell_texts[2]) if len(cell_texts) > 2 else 0,
                        "interest_amount": _parse_amount(cell_texts[3]) if len(cell_texts) > 3 else 0,
                        "total_amount": _parse_amount(cell_texts[4]) if len(cell_texts) > 4 else 0,
                        "ao_name": cell_texts[5] if len(cell_texts) > 5 else "",
                        "ao_jurisdiction": cell_texts[6] if len(cell_texts) > 6 else "",
                        "status": cell_texts[7] if len(cell_texts) > 7 else "outstanding",
                    }
                    results.append(record)

            logger.info(f"Scraped {len(results)} demand records")

        except Exception as e:
            logger.exception("Failed to scrape demands")

        return results

    async def scrape_all(self) -> dict:
        """Scrape both e-Proceedings and demands."""
        proceedings = await self.scrape_eproceedings()
        await _delay(2)
        demands = await self.scrape_demands()
        return {
            "pan": self._pan,
            "proceedings": proceedings,
            "demands": demands,
        }


def _parse_amount(text: str) -> float:
    """Parse an Indian-formatted currency string to float."""
    if not text:
        return 0.0
    cleaned = text.replace("₹", "").replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> Optional[date]:
    """Parse a date string from the portal (DD/MM/YYYY or DD-MM-YYYY)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------
# Database persistence
# ------------------------------------------------------------------

def save_scraped_data(db, pan: str, scraped: dict) -> dict:
    """Persist scraped data into the database. Returns sync summary."""
    from app.models import Client, Proceeding, Demand, SyncLog

    now = datetime.now()
    stats = {
        "proceedings_found": 0,
        "proceedings_new": 0,
        "demands_found": 0,
        "demands_new": 0,
        "errors": [],
    }

    # Ensure client record exists
    client = db.query(Client).filter(Client.pan == pan).first()
    if not client:
        client = Client(pan=pan, name=f"PAN {pan}", last_synced=now)
        db.add(client)
        db.flush()
    else:
        client.last_synced = now

    # Save proceedings
    for rec in scraped.get("proceedings", []):
        stats["proceedings_found"] += 1
        ay = rec.get("assessment_year", "").strip()
        section = rec.get("section", "").strip()
        portal_ref = rec.get("portal_ref_id", "").strip()

        if not ay:
            continue

        # Check for existing by portal_ref or (pan, ay, section)
        existing = None
        if portal_ref:
            existing = db.query(Proceeding).filter(
                Proceeding.pan == pan, Proceeding.portal_ref_id == portal_ref
            ).first()
        if not existing and section:
            existing = db.query(Proceeding).filter(
                Proceeding.pan == pan,
                Proceeding.assessment_year == ay,
                Proceeding.section == section,
            ).first()

        if existing:
            # Update
            if rec.get("status"):
                existing.status = rec["status"]
            if rec.get("response_due_date"):
                existing.response_due_date = _parse_date(rec["response_due_date"])
            existing.updated_at = now
        else:
            # Insert
            proc = Proceeding(
                pan=pan,
                assessment_year=ay,
                notice_type=rec.get("notice_type", "unknown"),
                section=section or "N/A",
                date_of_issue=_parse_date(rec.get("date_of_issue", "")),
                response_due_date=_parse_date(rec.get("response_due_date", "")),
                status=rec.get("status", "pending"),
                portal_ref_id=portal_ref or None,
            )
            db.add(proc)
            stats["proceedings_new"] += 1

    # Save demands
    for rec in scraped.get("demands", []):
        stats["demands_found"] += 1
        ay = rec.get("assessment_year", "").strip()
        section = rec.get("section", "").strip()

        if not ay:
            continue

        existing = db.query(Demand).filter(
            Demand.pan == pan,
            Demand.assessment_year == ay,
            Demand.section == section,
        ).first()

        if existing:
            existing.demand_amount = rec.get("demand_amount", existing.demand_amount)
            existing.interest_amount = rec.get("interest_amount", existing.interest_amount)
            existing.total_amount = rec.get("total_amount", existing.total_amount)
            existing.last_checked = now
            if rec.get("status"):
                existing.status = rec["status"]
        else:
            demand = Demand(
                pan=pan,
                assessment_year=ay,
                section=section or "N/A",
                demand_amount=rec.get("demand_amount", 0),
                interest_amount=rec.get("interest_amount", 0),
                total_amount=rec.get("total_amount", 0),
                ao_name=rec.get("ao_name", ""),
                ao_jurisdiction=rec.get("ao_jurisdiction", ""),
                status=rec.get("status", "outstanding"),
                last_checked=now,
            )
            db.add(demand)
            stats["demands_new"] += 1

    # Log the sync
    sync_log = SyncLog(
        pan=pan,
        sync_type="portal_scrape",
        records_found=stats["proceedings_found"] + stats["demands_found"],
        records_new=stats["proceedings_new"] + stats["demands_new"],
        records_changed=0,
        errors="; ".join(stats["errors"]) if stats["errors"] else None,
        status="success" if not stats["errors"] else "partial",
        started_at=now,
        completed_at=datetime.now(),
    )
    db.add(sync_log)

    db.commit()
    return stats
