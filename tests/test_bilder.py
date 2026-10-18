"""AI-bilder: kostnad, generering, fel och omförsök, Bild-fliken, omgenerering av text och WordPress.

Bildtjänsten är alltid mockad. Inga riktiga anrop görs och inga kostnader uppstår.
"""

import asyncio
import base64
import io
import json
import re
import time

import httpx
import pytest
from PIL import Image

from conftest import ny_installning
from test_publicering import FalskWordPress, imorgon, kl, kor


def png(bredd=1536, hojd=1024, farg=(200, 220, 210), lage="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(lage, (bredd, hojd), farg if lage == "RGB" else farg + (128,)).save(buf, "PNG")
    return buf.getvalue()


class FalskTjanst:
    """Mockad bildtjänst. fel: {text i prompten: [undantag eller 'vanta', ...]} som används i tur och ordning."""

    kalla = "ai"

    def __init__(self, fel=None):
        self.prompter: list[str] = []
        self.fel = fel or {}

    async def generera(self, prompt):
        from app import bilder

        self.prompter.append(prompt)
        for text, lista in self.fel.items():
            if text in prompt and lista:
                f = lista.pop(0)
                if f == "vanta":
                    await asyncio.sleep(5)
                raise f
        return png(farg=(len(self.prompter) * 20 % 255, 200, 210))

    def anrop_med(self, text):
        return sum(text in p for p in self.prompter)


class WordPressMedMedia(FalskWordPress):
    def __init__(self):
        super().__init__()
        self.media: dict[int, dict] = {}
        self.uppladdningar = 0
        self.media_andringar = 0

    def __call__(self, request):
        path = request.url.path.removeprefix("/wp-json")
        if path == "/wp/v2/media" and request.method == "POST":
            self.uppladdningar += 1
            mid = 500 + len(self.media)
            namn = re.search(r'filename="([^"]+)"', request.headers["content-disposition"]).group(1)
            url = f"https://klimathemmet.se/wp-content/uploads/2026/10/{namn}"
            bas = url.removesuffix(".webp")
            self.media[mid] = {
                "id": mid, "source_url": url, "alt_text": "", "mime": request.headers["content-type"],
                "storlek": len(request.content),
                "media_details": {"width": 1536, "height": 1024, "sizes": {
                    "thumbnail": {"source_url": f"{bas}-150x150.webp", "width": 150, "height": 150},
                    "medium_large": {"source_url": f"{bas}-768x512.webp", "width": 768, "height": 512},
                    "large": {"source_url": f"{bas}-1024x683.webp", "width": 1024, "height": 683},
                }},
            }
            return httpx.Response(201, json=self.media[mid])
        if path.startswith("/wp/v2/media/"):
            mid = int(path.rsplit("/", 1)[1])
            if mid not in self.media:
                return httpx.Response(404, json={"code": "rest_post_invalid_id"})
            if request.method == "POST":
                self.media_andringar += 1
                self.media[mid].update(json.loads(request.content))
            return httpx.Response(200, json=self.media[mid])
        return super().__call__(request)


@pytest.fixture()
def tjanst(klient, monkeypatch):
    from app import bilder

    t = FalskTjanst()
    monkeypatch.setattr(bilder, "tjanst", lambda: t)
    monkeypatch.setattr(bilder, "VANTETID_BAS", 0)
    return t


def vanta(villkor, sekunder=5):
    slut = time.time() + sekunder
    while time.time() < slut:
        if villkor():
            return
        time.sleep(0.02)
    raise AssertionError("tidsgränsen löpte ut")


def artikel_med_text(klient, sokord="ventilation i gamla hus", sek=("självdrag",)):
    from app import db

    a = klient.post("/api/artiklar", json={"huvudsokord": sokord, "sekundara_sokord": list(sek)}).json()
    klient.post(f"/api/artiklar/{a['id']}/generera")
    vanta(lambda: db.hamta(a["id"])["status"] == "granskas")
    return a["id"]


def bilder_klara(aid):
    from app import bilder, db

    vanta(lambda: not bilder.pagar(aid) and all(b.get("status") != "genereras" for b in db.hamta(aid)["innehall"]["bilder"]))
    return {b["id"]: b for b in db.hamta(aid)["innehall"]["bilder"]}


def bestall(klient, aid, ids=(1, 2, 3, 4)):
    return klient.post(f"/api/artiklar/{aid}/bilder/generera", json={"bild_ids": list(ids), "bekraftad": True})


# ---------- Kostnad och bekräftelse ----------

def test_kostnadsuppskattning_visas_och_ar_konfigurerbar(klient, monkeypatch):
    k = klient.get("/api/bilder/kostnad?antal=4").json()
    assert k["antal"] == 4 and k["modell"] == "gpt-image-2" and k["kvalitet"] == "medium"
    assert k["total_usd"] == pytest.approx(4 * 0.041) and k["total_sek"] == pytest.approx(4 * 0.041 * 10, abs=0.01)
    ny_installning(monkeypatch, STUDIO_BILD_PRIS_USD="0.25", STUDIO_USD_SEK="11")
    k = klient.get("/api/bilder/kostnad?antal=2").json()
    assert k["total_usd"] == 0.5 and k["total_sek"] == 5.5
    assert klient.get("/api/status").json()["bildkostnad"]["pris_per_bild_usd"] == 0.25


def test_ingen_generering_utan_bekraftelse_eller_bildtjanst(klient):
    aid = artikel_med_text(klient)
    r = klient.post(f"/api/artiklar/{aid}/bilder/generera", json={"bild_ids": [1, 2, 3, 4]})
    assert r.status_code == 400 and "kostnad" in r.json()["detail"]
    r = bestall(klient, aid)
    assert r.status_code == 409 and "OPENAI_API_KEY" in r.json()["detail"]
    assert klient.get("/api/status").json()["bilder"] is False


def test_nyckeln_lamnar_aldrig_servern(klient, monkeypatch):
    ny_installning(monkeypatch, OPENAI_API_KEY="sk-hemlig-nyckel-123")
    assert klient.get("/api/status").json()["bilder"] is True
    aid = artikel_med_text(klient)
    for svar in (klient.get("/api/status"), klient.get(f"/api/artiklar/{aid}"), klient.get("/api/bilder/kostnad"), klient.get("/")):
        assert "sk-hemlig" not in svar.text


# ---------- Generering ----------

def test_fyra_bilder_genereras_som_webp_med_motiv_filnamn_och_placering(klient, tjanst):
    from app import bilder

    aid = artikel_med_text(klient)
    r = bestall(klient, aid)
    assert r.status_code == 202 and r.json()["startade"] == [1, 2, 3, 4]
    assert r.json()["kostnad"]["total_usd"] == pytest.approx(0.164)
    b = bilder_klara(aid)
    assert len(tjanst.prompter) == 4
    for bid in range(1, 5):
        assert b[bid]["status"] == "klar" and b[bid]["kalla"] == "ai" and not b[bid]["fel"]
        fil = bilder.mapp(aid) / b[bid]["fil"]
        with Image.open(fil) as im:
            assert im.format == "WEBP" and im.size == (1536, 1024)
        svar = klient.get(b[bid]["url"])
        assert svar.status_code == 200 and svar.headers["content-type"] == "image/webp"
    # Stil och förbud mot text, logotyper och vattenstämplar finns i varje prompt.
    for p in tjanst.prompter:
        assert "photorealistic" in p and "Scandinavian" in p and "no text" in p and "logos" in p and "watermarks" in p
    assert tjanst.anrop_med("Hero image") == 1
    a = klient.get(f"/api/artiklar/{aid}").json()
    info = {int(k): v for k, v in a["bildinfo"].items()}
    assert info[1]["roll"] == "huvudbild" and info[1]["avsnitt"] == "Efter introduktionen"
    assert [info[i]["roll"] for i in (2, 3, 4)] == ["avsnitt"] * 3
    assert info[2]["avsnitt"].startswith("Vad innebär") and info[3]["avsnitt"] == "Vad kostar det?"
    assert all("ventilation-i-gamla-hus" in info[i]["filnamn"] and info[i]["filnamn"].endswith(".webp") for i in info)
    assert len({info[i]["filnamn"] for i in info}) == 4
    # Huvudbilden står efter introduktionen och de övriga direkt efter sina H2-rubriker.
    html_ = a["innehall"]["html"]
    assert html_.index("<!--BILD:1-->") > html_.index("kh-toc") and html_.index("<!--BILD:1-->") < html_.index("<h2")
    for bid in (2, 3, 4):
        assert re.search(rf"</h2>\s*<!--BILD:{bid}-->", html_)


def test_delvis_misslyckad_generering_behaller_klara_bilder_och_forsoker_igen(klient, tjanst):
    from app import bilder

    tjanst.fel = {"Close-up": [bilder.BildFel("Tillfälligt fel", kan_forsoka=True)],
                  "reviewing a quote": [bilder.BildFel("Bildtjänsten avböjde motivet.")]}
    aid = artikel_med_text(klient)
    bestall(klient, aid)
    b = bilder_klara(aid)
    assert tjanst.anrop_med("Close-up") == 2 and b[2]["status"] == "klar"
    # Ett fel som inte går att försöka om igen ger upp direkt; de andra bilderna sparas ändå.
    assert tjanst.anrop_med("reviewing a quote") == 1
    assert b[3]["status"] == "fel" and "avböjde" in b[3]["fel"] and not b[3].get("fil")
    assert all(b[i]["status"] == "klar" and (bilder.mapp(aid) / b[i]["fil"]).is_file() for i in (1, 2, 4))
    # Bara den misslyckade bilden beställs igen.
    bestall(klient, aid, [3])
    b2 = bilder_klara(aid)
    assert b2[3]["status"] == "klar" and all(b2[i]["fil"] == b[i]["fil"] for i in (1, 2, 4))


def test_timeout_och_misslyckad_omgenerering_behaller_tidigare_bild(klient, tjanst, monkeypatch):
    from app import bilder

    aid = artikel_med_text(klient)
    bestall(klient, aid, [2])
    fore = bilder_klara(aid)[2]
    ny_installning(monkeypatch, STUDIO_BILD_TIMEOUT="0.05")
    tjanst.fel = {"Close-up": ["vanta", "vanta", "vanta"]}
    bestall(klient, aid, [2])
    efter = bilder_klara(aid)[2]
    assert tjanst.anrop_med("Close-up") == 1 + bilder.MAX_FORSOK
    assert "inte i tid" in efter["fel"] and "3 försök" in efter["fel"]
    assert efter["status"] == "klar" and efter["fil"] == fore["fil"] and (bilder.mapp(aid) / fore["fil"]).is_file()


def test_omstart_mitt_i_generering_markerar_bilden(klient):
    from app import bilder, db

    aid = artikel_med_text(klient)

    def satt(inn):
        inn["bilder"][0]["status"] = "genereras"
        return inn
    db.andra_innehall(aid, satt, db.STATUSAR)
    bilder.init()
    b = db.hamta(aid)["innehall"]["bilder"][0]
    assert b["status"] == "fel" and "startades om" in b["fel"]


# ---------- Bild-fliken ----------

def test_alt_text_kan_redigeras_men_webblasaren_kan_inte_byta_fil(klient, tjanst):
    from app import db

    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    fore = bilder_klara(aid)[1]
    a = klient.get(f"/api/artiklar/{aid}").json()
    innehall = a["innehall"]
    innehall["bilder"][0] |= {"alt": "Ljus vardagsrumsmiljö med fönster på glänt i ett gammalt hus",
                              "url": "https://evil.example/bild.webp", "fil": None, "godkand": True}
    assert klient.put(f"/api/artiklar/{aid}/innehall", json=innehall).status_code == 200
    b = db.hamta(aid)["innehall"]["bilder"][0]
    assert b["alt"] == "Ljus vardagsrumsmiljö med fönster på glänt i ett gammalt hus"
    assert b["url"] == fore["url"] and b["fil"] == fore["fil"] and not b.get("godkand")
    # Att spara samma sak igen är ingen ändring.
    hist = len(db.historik(aid))
    klient.put(f"/api/artiklar/{aid}/innehall", json=klient.get(f"/api/artiklar/{aid}").json()["innehall"])
    assert len(db.historik(aid)) == hist


def test_byta_bild_manuellt(klient, tjanst):
    from app import bilder, db

    aid = artikel_med_text(klient)
    url = f"/api/artiklar/{aid}/bilder/2/ladda-upp"
    r = klient.post(url, content=png(1200, 800, lage="RGBA"), headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and r.json()["bredd"] == 1200
    b = db.hamta(aid)["innehall"]["bilder"][1]
    assert b["kalla"] == "manuell" and b["status"] == "klar"
    with Image.open(bilder.mapp(aid) / b["fil"]) as im:
        assert im.format == "WEBP" and im.mode == "RGB"
    assert klient.post(url, content=png(400, 300)).status_code == 400
    gif = io.BytesIO()
    Image.new("RGB", (1200, 800)).save(gif, "GIF")
    r = klient.post(url, content=gif.getvalue())
    assert r.status_code == 400 and "JPEG" in r.json()["detail"]
    assert klient.post(url, content=b"inte en bild").status_code == 400
    assert klient.post(url, content=b"0" * (bilder.MAX_UPPLADDNING + 1)).status_code == 413
    assert tjanst.prompter == []


def test_godkand_bild_genereras_aldrig_om_och_ar_last(klient, tjanst):
    aid = artikel_med_text(klient)
    bestall(klient, aid)
    bilder_klara(aid)
    assert klient.post(f"/api/artiklar/{aid}/bilder/1/godkann", json={"godkand": True}).status_code == 200
    r = bestall(klient, aid, [1, 2])
    assert r.status_code == 409 and "godkänd" in r.json()["detail"]
    assert klient.post(f"/api/artiklar/{aid}/bilder/1/ladda-upp", content=png()).status_code == 409
    assert klient.post(f"/api/artiklar/{aid}/bilder/1/godkann", json={"godkand": False}).status_code == 200
    assert bestall(klient, aid, [1]).status_code == 202
    bilder_klara(aid)


def test_omgenerering_av_text_behaller_bilderna(klient, tjanst):
    from app import db

    aid = artikel_med_text(klient)
    bestall(klient, aid)
    fore = bilder_klara(aid)
    klient.post(f"/api/artiklar/{aid}/bilder/1/godkann", json={"godkand": True})
    anrop = len(tjanst.prompter)
    assert klient.post(f"/api/artiklar/{aid}/generera").status_code == 202
    vanta(lambda: db.hamta(aid)["status"] == "granskas")
    efter = {b["id"]: b for b in db.hamta(aid)["innehall"]["bilder"]}
    assert len(tjanst.prompter) == anrop
    assert efter[1] == fore[1] | {"godkand": True}
    for bid in (2, 3, 4):
        assert efter[bid]["fil"] == fore[bid]["fil"] and efter[bid]["alt"] == fore[bid]["alt"] and efter[bid]["inaktuell"]


def test_godkannande_av_artikeln_godkanner_bilderna(klient, tjanst):
    from app import db

    aid = artikel_med_text(klient)
    bestall(klient, aid, [1, 2])
    bilder_klara(aid)
    assert klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True}).status_code == 200
    a = db.hamta(aid)
    assert [b.get("godkand", False) for b in a["innehall"]["bilder"]] == [True, True, False, False]
    assert a["godkand_hash"] == db.innehall_hash(a["innehall"])
    # En godkänd artikels bilder kan inte ändras utan att godkännandet återkallas.
    assert bestall(klient, aid, [3]).status_code == 409
    # Begärda ändringar och ny text: de godkända bilderna står kvar oförändrade.
    klient.post(f"/api/artiklar/{aid}/begar-andringar", json={"kommentar": "Kortare ingress"})
    klient.post(f"/api/artiklar/{aid}/generera")
    vanta(lambda: db.hamta(aid)["status"] == "granskas")
    assert [b["fil"] for b in db.hamta(aid)["innehall"]["bilder"][:2]] == [b["fil"] for b in a["innehall"]["bilder"][:2]]


def test_textgenerering_och_godkannande_vantar_pa_bilder(klient, tjanst, monkeypatch):
    from app import bilder

    tjanst.fel = {"Hero image": ["vanta"]}
    ny_installning(monkeypatch, STUDIO_BILD_TIMEOUT="0.5")
    monkeypatch.setattr(bilder, "MAX_FORSOK", 1)
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    assert klient.post(f"/api/artiklar/{aid}/generera").status_code == 409
    assert klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True}).status_code == 409
    assert bestall(klient, aid, [2]).status_code == 409
    bilder_klara(aid)


def test_bildfiler_tas_bort_med_artikeln(klient, tjanst):
    from app import bilder

    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    bilder_klara(aid)
    assert bilder.mapp(aid).is_dir()
    klient.delete(f"/api/artiklar/{aid}")
    assert not bilder.mapp(aid).exists()


def test_ogiltiga_filnamn_nekas(klient):
    assert klient.get("/api/bilder/1/..%2F..%2Fstudio.db").status_code == 404
    assert klient.get("/api/bilder/1/1-abc.webp").status_code == 404


# ---------- WordPress ----------

def test_publicering_laddar_upp_bilderna_och_satter_utvald_bild(klient, tjanst, monkeypatch):
    from app import db

    ny_installning(monkeypatch, WP_URL="https://klimathemmet.se", WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl",
                   STUDIO_PUBLICERING="skarp")
    wp = WordPressMedMedia()
    aid = artikel_med_text(klient)
    bestall(klient, aid)
    bilder_klara(aid)
    # Inget har skickats till WordPress under bildgenereringen.
    assert wp.uppladdningar == 0
    klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    kor(kl(dag), wp)
    a = db.hamta(aid)
    assert a["status"] == "publicerad"
    post = wp.inlagg[a["wp_post_id"]]
    assert wp.uppladdningar == 4 and all(m["mime"] == "image/webp" for m in wp.media.values())
    forsta = min(wp.media)
    assert post["featured_media"] == forsta
    assert wp.media[forsta]["alt_text"] == a["innehall"]["bilder"][0]["alt"]
    assert wp.media[forsta]["source_url"].startswith("https://klimathemmet.se/wp-content/uploads/2026/10/")
    assert "ventilation-i-gamla-hus" in wp.media[forsta]["source_url"]
    innehall = post["content"]
    assert "/api/bilder/" not in innehall
    figurer = re.findall(r"<img [^>]+>", innehall)
    assert len(figurer) == 4
    assert all('width="1536" height="1024"' in f and "srcset=" in f and "768w" in f and "150x150" not in f for f in figurer)
    assert 'fetchpriority="high"' in figurer[0] and 'loading="lazy"' not in figurer[0]
    assert all('loading="lazy"' in f for f in figurer[1:])


def test_bilder_laddas_inte_upp_tva_ganger_och_alt_text_uppdateras(klient, tjanst, monkeypatch):
    from app import db
    from app.wordpress import WordPress

    ny_installning(monkeypatch, WP_URL="https://klimathemmet.se", WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl")
    wp = WordPressMedMedia()
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1, 2])
    bilder_klara(aid)

    async def skicka():
        async with WordPress(transport=httpx.MockTransport(wp)) as k:
            return await k.spara_inlagg(db.hamta(aid), "draft")
    asyncio.run(skicka())
    asyncio.run(skicka())
    assert wp.uppladdningar == 2 and len(wp.inlagg) == 1
    innehall = klient.get(f"/api/artiklar/{aid}").json()["innehall"]
    innehall["bilder"][1]["alt"] = "Närbild på en ventil i en gammal tegelvägg i ett ljust rum"
    klient.put(f"/api/artiklar/{aid}/innehall", json=innehall)
    andringar = wp.media_andringar
    asyncio.run(skicka())
    assert wp.uppladdningar == 2 and wp.media_andringar == andringar + 1
    assert wp.media[501]["alt_text"] == "Närbild på en ventil i en gammal tegelvägg i ett ljust rum"
    # Bilden har tagits bort i mediabiblioteket: den laddas upp igen.
    del wp.media[500]
    asyncio.run(skicka())
    assert wp.uppladdningar == 3


def test_testbilder_kan_inte_skickas_till_wordpress(klient, monkeypatch):
    from app import db
    from app.wordpress import WordPress, WordPressFel

    ny_installning(monkeypatch, STUDIO_BILDTJANST="test", WP_URL="https://klimathemmet.se", WP_USER="moa",
                   WP_APP_PASSWORD="abcd efgh ijkl")
    assert klient.get("/api/bilder/kostnad?antal=4").json()["total_usd"] == 0
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    assert bilder_klara(aid)[1]["kalla"] == "test"
    wp = WordPressMedMedia()

    async def skicka():
        async with WordPress(transport=httpx.MockTransport(wp)) as k:
            return await k.spara_inlagg(db.hamta(aid), "draft")
    with pytest.raises(WordPressFel, match="testbilder"):
        asyncio.run(skicka())
    assert wp.uppladdningar == 0 and not wp.inlagg


def test_testtjansten_stoppas_i_produktion(monkeypatch):
    from app import config

    monkeypatch.setenv("STUDIO_BILDTJANST", "test")
    monkeypatch.setenv("STUDIO_MILJO", "produktion")
    s = config.Settings()
    assert not s.bilder_aktiva
    with pytest.raises(RuntimeError, match="STUDIO_BILDTJANST=test"):
        s.kontrollera_produktion()


def test_kopierad_kod_innehaller_aldrig_studions_bildadresser(klient, tjanst):
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    bilder_klara(aid)
    kod = klient.post("/api/exportera", json=klient.get(f"/api/artiklar/{aid}").json()["innehall"]).json()["kod"]
    assert "/api/bilder/" not in kod and "<!--BILD" not in kod


# ---------- OpenAI-klienten (mockad) ----------

def test_openai_klienten_skickar_ratt_anrop_och_tolkar_fel(klient, monkeypatch):
    from app import bilder

    ny_installning(monkeypatch, OPENAI_API_KEY="sk-test-nyckel", STUDIO_BILD_KVALITET="high")
    anrop = []
    svar = []

    def hantera(request):
        anrop.append(request)
        return svar.pop(0)

    monkeypatch.setattr(bilder.OpenAIBilder, "transport", httpx.MockTransport(hantera))
    t = bilder.OpenAIBilder()
    svar.append(httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}))
    data = asyncio.run(t.generera("A Swedish house"))
    assert Image.open(io.BytesIO(data)).size == (1536, 1024)
    body = json.loads(anrop[0].content)
    assert anrop[0].url == "https://api.openai.com/v1/images/generations"
    assert anrop[0].headers["authorization"] == "Bearer sk-test-nyckel"
    assert body | {"prompt": None} == {"model": "gpt-image-2", "prompt": None, "size": "1536x1024", "quality": "high", "n": 1,
                                       "output_format": "webp", "output_compression": 95}
    fall = [
        (httpx.Response(400, json={"error": {"code": "moderation_blocked", "message": "x"}}), "avböjde", False),
        (httpx.Response(401, json={"error": {"code": "invalid_api_key"}}), "OPENAI_API_KEY", False),
        (httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}), "för många", True),
        (httpx.Response(429, json={"error": {"code": "insufficient_quota"}}), "krediter", False),
        (httpx.Response(503), "tillfälligt", True),
        (httpx.Response(404, json={"error": {"code": "model_not_found"}}), "STUDIO_BILD_MODELL", False),
    ]
    for resp, text, kan in fall:
        svar.append(resp)
        with pytest.raises(bilder.BildFel) as e:
            asyncio.run(t.generera("x"))
        assert text in str(e.value) and e.value.kan_forsoka is kan and "sk-test" not in str(e.value)


def test_bilderna_kraver_inloggning(klient, tjanst, monkeypatch):
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    url = bilder_klara(aid)[1]["url"]
    ny_installning(monkeypatch, STUDIO_USER="moa", STUDIO_PASSWORD="ett-langt-losenord", STUDIO_SESSION_SECRET="x" * 40)
    assert klient.get(url).status_code == 401
    assert klient.post(f"/api/artiklar/{aid}/bilder/generera", json={"bild_ids": [2], "bekraftad": True}).status_code == 401


# ---------- Aktivering av riktiga bilder ----------

def test_gratis_kontroll_av_nyckel_och_modell_skapar_ingen_bild(klient, monkeypatch):
    from app import bilder

    r = klient.post("/api/bilder/kontrollera").json()
    assert r["ok"] is False and ".env" in r["meddelande"] and r["nyckel"] is False
    ny_installning(monkeypatch, OPENAI_API_KEY="sk-proj-hemlig-nyckel-456")
    anrop = []
    svar = []

    def hantera(request):
        anrop.append(request)
        return svar.pop(0)
    monkeypatch.setattr(bilder.OpenAIBilder, "transport", httpx.MockTransport(hantera))
    svar.append(httpx.Response(200, json={"id": "gpt-image-2", "object": "model"}))
    r = klient.post("/api/bilder/kontrollera")
    assert r.json()["ok"] is True and "Ingen bild skapades" in r.json()["meddelande"]
    assert anrop[0].method == "GET" and anrop[0].url == "https://api.openai.com/v1/models/gpt-image-2"
    for kod, data, text in [(401, {"error": {"code": "invalid_api_key"}}, "godkände inte"),
                            (404, {"error": {"code": "model_not_found"}}, "STUDIO_BILD_MODELL"),
                            (403, {}, "behörighet")]:
        svar.append(httpx.Response(kod, json=data))
        r = klient.post("/api/bilder/kontrollera")
        assert r.json()["ok"] is False and text in r.json()["meddelande"]
        assert "sk-proj-hemlig" not in r.text
    # Bara GET-anrop: ingen bild har beställts.
    assert all(a.method == "GET" and "/images/" not in str(a.url) for a in anrop)


def test_tydlig_orsak_nar_bilder_inte_ar_aktiva(klient, monkeypatch):
    assert "OPENAI_API_KEY saknas" in klient.get("/api/status").json()["bilder_orsak"]
    ny_installning(monkeypatch, OPENAI_API_KEY="sk-proj-x", STUDIO_BILDTJANST="av")
    assert "avstängd" in klient.get("/api/status").json()["bilder_orsak"]
    ny_installning(monkeypatch, STUDIO_BILDTJANST="openai")
    s = klient.get("/api/status").json()
    assert s["bilder"] is True and s["bilder_orsak"] is None


def test_startloggen_visar_aldrig_nyckeln(miljo, monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app import main

    ny_installning(monkeypatch, OPENAI_API_KEY="sk-proj-hemlig-nyckel-789")
    with caplog.at_level("INFO"), TestClient(main.app):
        pass
    rad = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Bilder:"))
    assert "openai" in rad and "gpt-image-2" in rad and "nyckel finns" in rad
    assert "sk-proj-hemlig" not in caplog.text


def test_env_filen_lases_och_en_tom_variabel_doljer_den_inte(tmp_path, monkeypatch):
    import importlib

    from app import config

    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-proj-fran-env-filen\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("STUDIO_ENV_FIL", str(env))
    monkeypatch.setenv("STUDIO_DOTENV", "1")
    try:
        importlib.reload(config)
        assert config.settings.openai_api_key == "sk-proj-fran-env-filen"
        assert config.settings.bilder_aktiva and config.settings.status()["bilder"] is True
        assert "sk-proj" not in json.dumps(config.settings.status()) and "sk-proj" not in repr(config.settings)
    finally:
        monkeypatch.setenv("STUDIO_DOTENV", "0")
        monkeypatch.undo()
        importlib.reload(config)


def test_kontrollskriptet_visar_aldrig_nyckeln(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    rot = Path(__file__).resolve().parent.parent
    bas = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI", "STUDIO_BILD"))}
    bas |= {"STUDIO_DOTENV": "0", "STUDIO_SECRETS_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "scripts/kontrollera_bilder.py"], cwd=rot, env=bas, capture_output=True, text=True)
    assert r.returncode == 1 and "OPENAI_API_KEY:     saknas" in r.stdout and "✗ OPENAI_API_KEY saknas" in r.stdout
    # Med testtjänsten görs inget nätverksanrop; nyckeln får ändå aldrig skrivas ut.
    r = subprocess.run([sys.executable, "scripts/kontrollera_bilder.py"], cwd=rot, capture_output=True, text=True,
                       env=bas | {"OPENAI_API_KEY": "sk-proj-hemlig-nyckel-000", "STUDIO_BILDTJANST": "test"})
    assert r.returncode == 0 and "hittad" in r.stdout and "sk-proj-hemlig" not in r.stdout + r.stderr
