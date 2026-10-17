"""Webbläsartester av gränssnittet på mobil, surfplatta och dator.

Körs med:  python -m pytest tests/e2e -q
Kräver:    pip install playwright && playwright install chromium
Skärmbilder sparas i E2E_SKARMBILDER om variabeln är satt.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
ENHETER = {
    "iphone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "device_scale_factor": 3},
    "android": {"viewport": {"width": 412, "height": 915}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2.6},
    "surfplatta": {"viewport": {"width": 820, "height": 1180}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2},
    "dator": {"viewport": {"width": 1440, "height": 900}},
}
MOBIL = {"iphone", "android"}
SOKORD = {"iphone": "ventilation i gamla hus", "android": "fukt i krypgrund", "surfplatta": "tilläggsisolering vind", "dator": "bergvärme kostnad"}


def _ledig_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    port = _ledig_port()
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "STUDIO_USER", "STUDIO_PASSWORD")}
    env.update(STUDIO_DB=str(tmp / "e2e.db"), STUDIO_SECRETS_DIR=str(tmp / "secrets"), STUDIO_CLAUDE_EFFORT="high")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        path = os.getenv("PLAYWRIGHT_CHROMIUM") or (
            "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
        )
        b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        yield b
        b.close()


def _skarmbild(page, namn):
    if mapp := os.getenv("E2E_SKARMBILDER"):
        Path(mapp).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f"{mapp}/{namn}.png", full_page=True)


def _ingen_sidscroll(page):
    bredd = page.evaluate("document.documentElement.scrollWidth")
    assert bredd <= page.viewport_size["width"], f"sidan är {bredd}px bred"


@pytest.mark.parametrize("enhet", list(ENHETER))
def test_granskningsflode(server, browser, enhet):
    ctx = browser.new_context(**ENHETER[enhet])
    page = ctx.new_page()
    fel = []
    page.on("pageerror", lambda e: fel.append(str(e)))
    page.goto(server)

    # Lägg till en artikel i kön och generera den (demotext utan API-nyckel).
    sokord = SOKORD[enhet]
    page.fill("#qHuvud", sokord)
    page.fill("#qChipInput", "självdrag, fukt")
    page.press("#qChipInput", "Enter")
    page.click("#addForm button[type=submit]")
    rad = page.locator(".q-item").filter(has=page.locator(".q-kw", has_text=sokord))
    rad.locator("[data-act=generate]").click()
    rad.locator(".s-granskas").wait_for()
    _ingen_sidscroll(page)
    if enhet in MOBIL:
        assert page.locator(".views").is_visible(), "bottennavigeringen syns inte"
        storlek = page.eval_on_selector("#qHuvud", "e => parseFloat(getComputedStyle(e).fontSize)")
        assert storlek >= 16, "formulärfält under 16px gör att iOS zoomar in"
    _skarmbild(page, f"{enhet}-1-ko")

    # Öppna artikeln för granskning.
    rad.locator("[data-act=open]").click()
    page.locator("#statusBadge.s-granskas").wait_for()
    page.wait_for_function("document.querySelector('#previewFrame').offsetHeight > 1200")
    _ingen_sidscroll(page)
    knapp = page.locator("#approveBtn")
    assert knapp.is_visible()
    if enhet in MOBIL:
        # Granskningsknapparna ska ligga inom skärmen utan att man behöver scrolla.
        box = knapp.bounding_box()
        assert box["y"] + box["height"] <= page.viewport_size["height"]
    _skarmbild(page, f"{enhet}-2-granska")

    # Godkännande kräver att kryssrutan bockas i.
    knapp.click()
    assert page.locator("#approveOk").is_disabled()
    _skarmbild(page, f"{enhet}-3-godkann-dialog")
    page.locator("#approveConfirm").check()
    page.locator("#approveOk").click()
    page.locator("#statusBadge.s-godkand").wait_for()

    # Begär ändringar via dialogen.
    page.click("#changesBtn")
    page.fill("#dialogComment", "Kortare ingress")
    page.click("#dialogOk")
    page.locator("#statusBadge.s-utkast").wait_for()
    assert "Kortare ingress" in page.text_content("#notice")

    # SEO-fliken går att nå och visa.
    page.click(".tab[data-tab=seo]")
    assert page.locator("#meta_titel").is_visible()
    _ingen_sidscroll(page)
    _skarmbild(page, f"{enhet}-4-seo")

    # Tillbaka till kön.
    page.click("#backBtn")
    page.locator("#queueView").wait_for()
    assert fel == []
    ctx.close()
