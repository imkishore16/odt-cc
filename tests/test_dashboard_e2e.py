"""End-to-end test of the ODT-CC web dashboard in headless Chrome (Playwright).

Usage:  python tests/test_dashboard_e2e.py standalone   # dashboard runs its own clock (Kit idle or closed)
        python tests/test_dashboard_e2e.py kit          # dashboard drives a running Kit
Needs:  the brain service on :8000; pip install playwright (uses the installed Google Chrome).
Screenshots go to $ODTCC_TEST_OUT (default /tmp/odtcc_dashboard_test).
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

MODE = sys.argv[1]
OUT = os.environ.get("ODTCC_TEST_OUT", "/tmp/odtcc_dashboard_test")
os.makedirs(OUT, exist_ok=True)
URL = "http://localhost:8000/dashboard/" + ("?standalone=1" if MODE == "standalone" else "")
errors = []


def minute(page):
    return int(page.inner_text("#minute").split()[-1])


def wait_for(page, cond, what, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return time.time() - t0
        page.wait_for_timeout(250)
    page.screenshot(path=f"{OUT}/dash_{MODE}_FAILED.png", full_page=True)
    raise AssertionError(f"timed out waiting for {what} (minute {minute(page)})")


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        errors.append(msg)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1100})
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
    page.goto(URL)
    page.wait_for_timeout(1500)
    badge = page.inner_text("#mode")
    print("mode badge:", badge)
    check(("Standalone" in badge) == (MODE == "standalone"), f"mode badge matches {MODE}")

    # ---------------------------------------------------------- normal scenario: alert -> card -> approve
    page.select_option("#scenario", "normal")
    page.wait_for_timeout(1500)
    page.click("#reset")
    wait_for(page, lambda: minute(page) == 0, "minute 0 after reset", 20)
    page.click("#play")
    wait_for(page, lambda: page.query_selector("#approve-ai") is not None, "recommendation card", 90)
    m = minute(page)
    card = page.inner_text("#card")
    check(m in (2, 3, 4), f"auto-paused at minute {m}")
    check("Recommendation" in card and "/100" in card, "card with scored options")
    page.screenshot(path=f"{OUT}/dash_{MODE}_1_card.png", full_page=True)
    page.click("#approve-ai")
    wait_for(page, lambda: minute(page) >= m + 3, "playback resumes after approval", 30)
    check("Plan in force" in page.inner_text("#banners") or "WARNING" in page.inner_text("#banners"), "banner after approval")
    check("Approved the AI pick" in page.inner_text("#log"), "decision log records the AI-pick approval")
    page.screenshot(path=f"{OUT}/dash_{MODE}_2_approved.png", full_page=True)

    # ---------------------------------------------------------- live: inject -> re-plan -> approve another option
    page.click("#pause")
    page.select_option("#scenario", "live")
    wait_for(page, lambda: "Live" in page.inner_text("#scenario-desc") or "world agent" in page.inner_text("#scenario-desc"),
             "live scenario", 30)
    wait_for(page, lambda: minute(page) == 0, "minute 0 in live", 20)
    page.click("#play")
    wait_for(page, lambda: page.query_selector("#approve-ai") is not None and not page.is_disabled("#approve-ai"),
             "first live card", 90)
    page.click("#approve-ai")
    m = minute(page)
    wait_for(page, lambda: minute(page) >= m + 2, "resume", 30)
    page.click("#pause")
    page.wait_for_timeout(800)
    check(page.is_visible("#inject-btn"), "inject control visible in live")
    page.select_option("#inject-kind", "rail_signal_failure")
    page.click("#inject-btn")
    page.wait_for_timeout(800)
    page.click("#play")
    wait_for(page, lambda: "Re-plan" in page.inner_text("#card"), "re-plan card after the incident", 90)
    card = page.inner_text("#card")
    check("keeping the current plan" in card, "re-plan compares against keeping the current plan")
    page.screenshot(path=f"{OUT}/dash_{MODE}_3_replan.png", full_page=True)
    buttons = page.query_selector_all("[data-approve]")
    pick = page.inner_text("#card").split("Re-plan: ")[1].split("\n")[0].strip().replace(" ", "_")
    other = next(b for b in buttons if b.get_attribute("data-approve") != pick and not b.is_disabled())
    other.click()
    wait_for(page, lambda: "not the AI pick" in page.inner_text("#log"), "log labels a non-AI option", 30)
    check(True, "approved a non-AI option and the log says so")
    page.click("#pause")

    # ---------------------------------------------------------- planning: compare with planned-event inputs
    page.select_option("#scenario", "normal")
    page.wait_for_timeout(2500)
    page.fill("#lv-train_interval_min", "2")
    page.dispatch_event("#lv-train_interval_min", "input")
    page.click("#simulate")
    wait_for(page, lambda: "UNSAFE" in page.inner_text("#my-result"), "Your plan verdict", 30)
    check("22 dangerous" in page.inner_text("#my-result") or "UNSAFE" in page.inner_text("#my-result"),
          "double the trains is UNSAFE")
    page.click("#save-a")
    page.click("#rec-now")
    wait_for(page, lambda: not page.is_disabled("#save-ai"), "AI card for planning", 60)
    page.click("#save-ai")
    for key, value in {"stagger_share": "0.3", "stagger_delay": "15", "nearby_event_people": "12000",
                       "nearby_event_minute": "15", "train_interval": "5", "train_capacity": "900"}.items():
        page.fill(f"#pl-{key}", value)
        page.dispatch_event(f"#pl-{key}", "input")
    page.check("#pl-close_vermont")
    page.click("#compare")
    wait_for(page, lambda: "Verdict" in page.inner_text("#compare-table"), "comparison table", 30)
    table = page.inner_text("#compare-table")
    check("Do nothing" in table and "Plan A" in table and "AI:" in table, "table has baseline, Plan A and AI columns")
    check("Vermont" in table and "second wave" in table, "planned-event notes shown")
    page.screenshot(path=f"{OUT}/dash_{MODE}_4_compare.png", full_page=True)
    browser.close()

print("\nERRORS:" if errors else "\nno errors", *errors, sep="\n  ")
sys.exit(1 if errors else 0)
