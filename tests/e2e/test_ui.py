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


def _starta(tmp, **extra):
    port = _ledig_port()
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "STUDIO_USER", "STUDIO_PASSWORD", "WP_URL", "STUDIO_PUBLICERING")}
    # STUDIO_DOTENV=0: servern läser inte in din .env (se tests/conftest.py).
    env.update(STUDIO_DOTENV="0", STUDIO_DATA=str(tmp), STUDIO_DB=str(tmp / "e2e.db"), STUDIO_SECRETS_DIR=str(tmp / "secrets"),
               STUDIO_CLAUDE_EFFORT="high", **extra)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return proc, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, url = _starta(tmp_path_factory.mktemp("e2e"))
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def skyddad_server(tmp_path_factory):
    proc, url = _starta(tmp_path_factory.mktemp("e2e-login"), STUDIO_USER="moa",
                        STUDIO_PASSWORD="ett-langt-losenord", STUDIO_SESSION_SECRET="x" * 40)
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
    # Vänta tills förhandsvisningen vuxit till artikelns höjd (wait_for_function stoppas av sidans CSP).
    for _ in range(100):
        if page.eval_on_selector("#previewFrame", "e => e.offsetHeight") > 1200:
            break
        page.wait_for_timeout(50)
    else:
        pytest.fail("förhandsvisningen fick aldrig artikelns höjd")
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


@pytest.mark.parametrize("enhet", ["iphone", "dator"])
def test_inloggning_och_publicering(skyddad_server, browser, enhet):
    ctx = browser.new_context(**ENHETER[enhet])
    page = ctx.new_page()
    fel = []
    page.on("pageerror", lambda e: fel.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    page.goto(skyddad_server)
    assert page.url.endswith("/login")
    page.fill("#user", "moa")
    page.fill("#password", "fel-losenord")
    page.click("#loginForm button")
    page.locator("#loginError").wait_for()
    page.fill("#password", "ett-langt-losenord")
    page.click("#loginForm button")
    page.wait_for_url(skyddad_server + "/")
    _skarmbild(page, f"{enhet}-5-inloggad")

    # Skapa och godkänn en artikel.
    sokord = f"golvvärme {enhet}"
    page.fill("#qHuvud", sokord)
    page.click("#addForm button[type=submit]")
    rad = page.locator(".q-item").filter(has=page.locator(".q-kw", has_text=sokord))
    rad.locator("[data-act=generate]").click()
    rad.locator("[data-act=open]").click()
    page.locator("#statusBadge.s-granskas").wait_for()
    page.click("#approveBtn")
    page.locator("#approveConfirm").check()
    page.locator("#approveOk").click()
    page.locator("#statusBadge.s-godkand").wait_for()

    # Schemalägg i publiceringsvyn.
    page.click('.view-btn[data-view="pub"]') if page.locator("#backBtn").is_hidden() else page.click("#backBtn")
    page.click('.view-btn[data-view="pub"]')
    page.locator("#modeBanner.simulering").wait_for()
    page.click("#scheduleBtn")
    page.locator(".pub-item .status-badge.s-schemalagd").first.wait_for()
    assert page.locator(".cal-ev.planerad").count() >= 1 or page.locator(".cal-ev").count() >= 1
    _ingen_sidscroll(page)
    _skarmbild(page, f"{enhet}-6-publicering")

    # Pausa och logga ut.
    page.locator("label.toggle", has=page.locator("#pauseToggle")).click()
    page.locator("#modeBanner.pausad").wait_for()
    page.locator("label.toggle", has=page.locator("#pauseToggle")).click()
    page.locator("#modeBanner.simulering").wait_for()
    page.click("#logoutBtn")
    page.wait_for_url("**/login")
    assert fel == []
    ctx.close()


@pytest.fixture(scope="module")
def sajt_server(tmp_path_factory):
    """Låtsas-WordPress över HTTP och en studio som pekar på den."""
    port = _ledig_port()
    bas = f"http://127.0.0.1:{port}"
    sajt = subprocess.Popen(
        [sys.executable, "-c", "import sys, uvicorn; sys.path.insert(0, 'tests'); from falsk_sajt import asgi_app; "
         f"uvicorn.run(asgi_app('{bas}'), port={port}, log_level='warning')"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc, url = _starta(tmp_path_factory.mktemp("e2e-seo"), WP_URL=bas)
    yield url
    for p in (proc, sajt):
        p.terminate()
        p.wait(timeout=10)


@pytest.mark.parametrize("enhet", ["iphone", "surfplatta", "dator"])
def test_seo_karta(sajt_server, browser, enhet):
    ctx = browser.new_context(**ENHETER[enhet])
    page = ctx.new_page()
    fel = []
    page.on("pageerror", lambda e: fel.append(str(e)))
    page.goto(sajt_server)
    page.click('.view-btn[data-view="seo"]')
    page.locator("#mapBtn:not([disabled])").wait_for()
    page.click("#mapBtn")
    page.locator("#mapBtn:not([disabled])").wait_for()  # kartläggningen är klar
    page.locator("#graph .nod").first.wait_for()
    assert page.locator("#graph .nod").count() == 8
    assert page.locator(".stat b").first.text_content() == "8"
    _ingen_sidscroll(page)
    _skarmbild(page, f"{enhet}-7-seokarta")

    # Klicka på pelarsidan i listan och se dess länkar.
    page.locator("#nodeTable tr", has_text="Ventilation i gamla hus").first.click()
    page.locator("#seoDetail h3", has_text="Ventilation i gamla hus").wait_for()
    assert "Inkommande länkar (3)" in page.text_content("#seoDetail")
    assert "trasig länk" in page.text_content("#seoDetail")

    # Klusterfärger, riktningspilar och översikt.
    assert page.locator("#clusterLegend li").count() == 2
    assert page.locator("#graph path.pilspets").count() >= 5
    assert "isolerade sidor" in page.text_content("#seoStats") and "väntande länkförslag" in page.text_content("#seoStats")
    page.select_option("#fIn", "0")
    assert page.locator("#nodeTable tr").count() == 4  # tre sidor utan inkommande länkar och kategorin Fukt
    page.select_option("#fIn", "")

    # Filtrera på sidor utan inkommande länkar.
    page.select_option("#fStatus", "foraldralos")
    assert page.locator("#nodeTable tr").count() == 3
    assert page.locator("#graph .nod.dold").count() == 5
    assert "Verifierat" in page.text_content("#seoContent") and "Beräknat" in page.text_content("#seoContent")
    assert fel == []
    ctx.close()


def test_ny_artikel_far_internlankar_fran_kartan(sajt_server, browser):
    ctx = browser.new_context(**ENHETER["dator"])
    page = ctx.new_page()
    page.goto(sajt_server)
    page.click('.view-btn[data-view="seo"]')
    if page.locator("#seoEmpty").is_visible():
        page.click("#mapBtn")
        page.locator("#seoContent").wait_for()
    page.click('.view-btn[data-view="queue"]')
    page.fill("#qHuvud", "ventilation och fukt")
    page.click("#addForm button[type=submit]")
    rad = page.locator(".q-item").filter(has=page.locator(".q-kw", has_text="ventilation och fukt"))
    rad.locator("[data-act=generate]").click()
    rad.locator("[data-act=open]").click()
    page.locator("#statusBadge.s-granskas").wait_for()
    page.click(".tab[data-tab=seo]")
    assert page.locator("#linksCard").is_visible()
    assert page.locator("#linksList li").count() >= 1
    assert page.locator("#linksList li.bad").count() == 0
    assert "internlänkar" in page.text_content("#checks")
    _skarmbild(page, "dator-8-internlankar")
    ctx.close()


@pytest.mark.parametrize("enhet", ["iphone", "dator"])
def test_lankforslag_kraver_godkannande(sajt_server, browser, enhet):
    ctx = browser.new_context(**ENHETER[enhet])
    page = ctx.new_page()
    fel = []
    page.on("pageerror", lambda e: fel.append(str(e)))
    page.goto(sajt_server)
    page.click('.view-btn[data-view="seo"]')
    page.locator("#mapBtn:not([disabled])").wait_for()
    page.click("#mapBtn")
    page.locator("#mapBtn:not([disabled])").wait_for()
    page.locator("#graph .nod").first.wait_for()

    page.click("#calcLinksBtn")
    page.locator("#linkList .lf").first.wait_for()
    forsta = page.locator("#linkList .lf").first
    assert forsta.locator("mark").count() == 1  # förhandsgranskning av exakt var länken hamnar
    assert "Beräknat" in forsta.text_content()
    assert page.locator("#applyLinksBtn").is_disabled()  # inget är godkänt ännu
    _ingen_sidscroll(page)
    _skarmbild(page, f"{enhet}-9-lankforslag")

    page.check("#checkAll")
    page.click("#approveLinksBtn")
    page.locator("#applyLinksBtn:not([disabled])").wait_for()
    page.click("#applyLinksBtn")
    assert page.locator("#applyOk").is_disabled()
    assert page.locator("#applyList li").count() >= 1
    _skarmbild(page, f"{enhet}-10-uppdatera-dialog")
    page.check("#applyConfirm")
    page.click("#applyOk")
    page.locator("#toast", has_text="simulerade").wait_for()
    page.click('[data-lankfilter="ovriga"]')
    assert page.locator("#linkList .lf", has_text="Simulerad").count() >= 1
    _ingen_sidscroll(page)

    # Detaljvyn visar huvudnyckelord.
    page.locator("#nodeTable tr", has_text="FTX-ventilation i villa").first.click()
    page.locator("#seoDetail", has_text="Huvudnyckelord").wait_for()
    assert fel == []
    ctx.close()


def test_seo_karta_utan_wordpress_adress(server, browser):
    """Utan WP_URL ska kartan förklara vad som saknas och inte gå att starta."""
    ctx = browser.new_context(**ENHETER["iphone"])
    page = ctx.new_page()
    page.goto(server)
    page.click('.view-btn[data-view="seo"]')
    page.locator("#seoEmpty").wait_for()
    assert "Ange sajtens adress (WP_URL)" in page.text_content("#seoEmptyText")
    assert page.locator("#mapBtn").is_disabled()
    ctx.close()
