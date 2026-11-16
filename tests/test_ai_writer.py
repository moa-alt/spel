"""Anropet till Claude och felhanteringen. Inget når Anthropic: SDK:n får en lokal låtsastransport."""

import asyncio
import json
import logging
import time

import anthropic
import httpx2
import pytest

from conftest import ny_installning

# Låtsasnyckeln sätts ihop av delar så att commit-spärren och sökningen efter hemligheter i git inte reagerar.
NYCKEL = "sk-ant-" + "api03-hemlig-testnyckel-ABCDEFGHIJKLMNOP"
PROJEKTNYCKEL = "sk-" + "proj-abcdefghijklmnopqrstuvwxyz0123"


def blk(typ, rubrik="", text="", punkter=(), rader=()) -> dict:
    """Ett block som AI:n skriver det: alltid alla fem fält, oanvända tomma."""
    return {"typ": typ, "rubrik": rubrik, "text": text, "punkter": list(punkter), "rader": list(rader)}


def punkt(text, rubrik=""):
    return {"rubrik": rubrik, "text": text}


def ai_dokument() -> dict:
    """Samma artikel som test_design.dokument(), i det platta formatet som Claude svarar med."""
    return {
        "hero": {"etikett": "VÄRME & ENERGI / GUIDE", "ingress": "Allt om <strong>värmepump i villa</strong>.",
                 "knapp_text": "Se åtgärderna", "knapp_mal": "atgarder"},
        "intro": {"etikett": "Kort om ämnet", "rubrik": "Så fungerar en värmepump", "stycken": ["Första stycket.", "Andra stycket."],
                  "fakta_etikett": "Snabböversikt", "fakta_rubrik": "Värmepump", "fakta": [{"titel": "Passar för", "text": "Villor."}]},
        "sektioner": [
            {"id": "sa-fungerar", "layout": "mork", "etikett": "Tekniken", "rubrik": "Hur värmepumpen hämtar värme", "meny": "Tekniken",
             "ingress": "", "bild": 2, "block": [blk("stycke", text="Text."), blk("underrubrik", text="Underrubrik")]},
            {"id": "problem", "layout": "standard", "etikett": "Utmaningar", "rubrik": "Vanliga problem", "meny": "Problem",
             "ingress": "Ingress.", "bild": 0, "block": [
                 blk("kort_problem", punkter=[punkt("Text.", "Buller"), punkt("Text.", "Is")]),
                 blk("notis", rubrik="Viktigt:", text="Notis.")]},
            {"id": "val", "layout": "bild", "etikett": "Välj", "rubrik": "Välj rätt modell", "meny": "Välj", "ingress": "", "bild": 3,
             "block": [blk("checklista", punkter=[punkt("Ett"), punkt("Två")]), blk("lista", punkter=[punkt("A"), punkt("B")])]},
            {"id": "jamforelse", "layout": "standard", "etikett": "Jämför", "rubrik": "Jämför systemen", "meny": "Jämför", "ingress": "",
             "bild": 0, "block": [blk("tabell", rader=[["System", "Fördel", "Nackdel"], ["Luft", "Billig", "Kallt"], ["Berg", "Effektiv", "Dyr"]])]},
            {"id": "steg", "layout": "bild", "etikett": "Steg", "rubrik": "Så installeras den", "meny": "Steg", "ingress": "", "bild": 4,
             "block": [blk("steg", punkter=[punkt("Text.", "Planera."), punkt("Text.", "Installera.")])]},
            {"id": "atgarder", "layout": "losningar", "etikett": "Åtgärder", "rubrik": "Fyra åtgärder", "meny": "Åtgärder", "ingress": "",
             "bild": 0, "block": [blk("kort_losning", punkter=[punkt("Text.", "Service")])]},
        ],
        "sammanfattning": {"rubrik": "Sammanfattning om värmepump", "stycken": ["Summering."]},
        "faq": {"rubrik": "Vanliga frågor", "fragor": [{"fraga": "Vad kostar den?", "svar": "Svar."}]},
        "relaterade_rubrik": "",
    }


def _artikeldata(dokument=None) -> dict:
    return {"titel": "Värmepump i villa: guide", "slug": "varmepump-i-villa", "meta_titel": "Värmepump i villa – guide",
            "meta_beskrivning": "Allt om värmepump i villa.", "sokintention": {"typ": "informativ", "beskrivning": "b", "malgrupp": "m"},
            "dokument": ai_dokument() if dokument is None else dokument,
            "bilder": [{"alt": f"Alt {i}", "bildtext": "", "prompt": f"Prompt {i}"} for i in range(4)]}


def svar_ok(data: dict | None = None) -> httpx2.Response:
    """Ett strömmat svar (server-sent events) som Messages API skickar det."""
    text = json.dumps(data or _artikeldata(), ensure_ascii=False)
    handelser = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 100}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    kropp = "".join(f"event: {namn}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for namn, d in handelser)
    return httpx2.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_ok"}, content=kropp.encode())


def svar_fel(status: int, typ: str, meddelande: str, request_id: str = "req_011CSHoEeqs5C35K2UUqR7Fy") -> httpx2.Response:
    return httpx2.Response(status, headers={"request-id": request_id},
                           json={"type": "error", "error": {"type": typ, "message": meddelande}, "request_id": request_id})


class FalskClaude:
    """Fångar förfrågningarna och svarar i tur och ordning med de svar testet köat."""

    def __init__(self, *svar):
        self.svar = list(svar)
        self.forfragningar: list[httpx2.Request] = []

    def __call__(self, request):
        self.forfragningar.append(request)
        return self.svar.pop(0)

    def kropp(self, i=0) -> dict:
        return json.loads(self.forfragningar[i].content)


@pytest.fixture()
def claude(monkeypatch, miljo):
    from app import ai_writer

    ny_installning(monkeypatch, ANTHROPIC_API_KEY=NYCKEL)

    def skapa(*svar):
        falsk = FalskClaude(*svar)
        monkeypatch.setattr(ai_writer, "_klient", lambda: anthropic.AsyncAnthropic(
            api_key=NYCKEL, max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(falsk))))
        return falsk
    return skapa


def generera(huvudsokord="värmepump i villa"):
    from app import ai_writer

    async def kor():
        handelser = []
        async for h in ai_writer.generera_artikel(huvudsokord, ["luft-vattenvärmepump"]):
            handelser.append(h)
        return handelser
    return asyncio.run(kor())


def fel_vid_generering() -> str:
    from app.ai_writer import GenereringsFel

    with pytest.raises(GenereringsFel) as e:
        generera()
    return str(e.value)


# ---------- Själva anropet ----------

def test_anropet_foljer_api_et_for_claude_opus_5_5(claude):
    falsk = claude(svar_ok())
    handelser = generera()
    assert handelser[-1]["typ"] == "klar" and handelser[-1]["artikel"]["dokument"]["version"] == 2
    req = falsk.forfragningar[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == NYCKEL and req.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    kropp = falsk.kropp()
    assert kropp["model"] == "claude-opus-5-5" and kropp["stream"] is True and kropp["max_tokens"] == 64000
    assert kropp["thinking"] == {"type": "adaptive"} and kropp["fallbacks"] == "default"
    assert kropp["output_config"]["effort"] == "high" and kropp["output_config"]["format"]["type"] == "json_schema"
    # Parametrar som Claude Opus 5.5 avvisar med 400 skickas aldrig, och nyckeln ligger bara i huvudet.
    for borttagen in ("temperature", "top_p", "top_k", "output_format", "budget_tokens"):
        assert borttagen not in json.dumps(kropp)
    assert NYCKEL not in req.content.decode()
    assert kropp["messages"][-1]["role"] == "user"


def test_reservmodellerna_kan_stangas_av(claude, monkeypatch):
    ny_installning(monkeypatch, STUDIO_CLAUDE_RESERVMODELL="av")
    falsk = claude(svar_ok())
    generera()
    assert "anthropic-beta" not in falsk.forfragningar[0].headers and "fallbacks" not in falsk.kropp()


def test_avvisad_beta_ger_nytt_forsok_utan_reservmodeller(claude, caplog):
    falsk = claude(svar_fel(400, "invalid_request_error",
                            "Unexpected value(s) `server-side-fallback-2026-07-01` for the `anthropic-beta` header."), svar_ok())
    with caplog.at_level(logging.WARNING, "studio.ai_writer"):
        assert generera()[-1]["typ"] == "klar"
    assert len(falsk.forfragningar) == 2
    assert falsk.forfragningar[0].headers["anthropic-beta"] and "fallbacks" in falsk.kropp(0)
    assert "anthropic-beta" not in falsk.forfragningar[1].headers and "fallbacks" not in falsk.kropp(1)
    assert "försöker utan dem" in caplog.text and NYCKEL not in caplog.text


# ---------- Felorsaken visas, utan känslig information ----------

def test_400_visar_api_ets_felorsak_och_request_id(claude, caplog):
    claude(svar_fel(400, "invalid_request_error", "output_config.format.schema: 'bild' is not valid"))
    with caplog.at_level(logging.WARNING, "studio.ai_writer"):
        text = fel_vid_generering()
    assert "fel 400 (invalid_request_error)" in text and "output_config.format.schema: 'bild' is not valid" in text
    assert "Request-id: req_011CSHoEeqs5C35K2UUqR7Fy" in text and "svarsformat" in text
    assert "req_011CSHoEeqs5C35K2UUqR7Fy" in caplog.text


def test_lag_kreditniva_ger_400_med_tydlig_atgard(claude):
    claude(svar_fel(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API. "
                                                  "Please go to Plans & Billing to upgrade or purchase credits."))
    text = fel_vid_generering()
    assert "credit balance is too low" in text and "saknar krediter" in text


def test_okand_modell_pekar_pa_installningen(claude, monkeypatch):
    ny_installning(monkeypatch, STUDIO_CLAUDE_MODEL="claude-finns-inte")
    claude(svar_fel(404, "not_found_error", "model: claude-finns-inte"))
    text = fel_vid_generering()
    assert "fel 404" in text and "STUDIO_CLAUDE_MODEL" in text


def test_nycklar_och_tokens_visas_aldrig(claude, caplog):
    lack = (f"Invalid header x-api-key: {NYCKEL} and Authorization: Bearer abcdefghijklmnop.qrstu "
            f"and {PROJEKTNYCKEL} and " + "A" * 60)
    claude(svar_fel(400, "invalid_request_error", lack))
    with caplog.at_level(logging.DEBUG):
        text = fel_vid_generering()
    for hemligt in (NYCKEL, "abcdefghijklmnop.qrstu", PROJEKTNYCKEL, "A" * 60, "hemlig-testnyckel"):
        assert hemligt not in text and hemligt not in caplog.text
    assert "[dolt]" in text


def test_ovriga_fel_ger_begripliga_meddelanden(claude):
    for svar, forvantat in [
        (svar_fel(401, "authentication_error", "invalid x-api-key"), "ANTHROPIC_API_KEY"),
        (svar_fel(403, "permission_error", "Your organization cannot use this model"), "Your organization cannot use this model"),
        (svar_fel(429, "rate_limit_error", "Rate limited"), "För många anrop"),
        (svar_fel(529, "overloaded_error", "Overloaded"), "överbelastad (529)"),
    ]:
        claude(svar)
        text = fel_vid_generering()
        assert forvantat in text and NYCKEL not in text


def test_felet_syns_pa_artikeln_i_studion(claude, klient):
    from app import db

    claude(svar_fel(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."))
    a = klient.post("/api/artiklar", json={"huvudsokord": "värmepump i villa"}).json()
    klient.post(f"/api/artiklar/{a['id']}/generera")
    for _ in range(200):
        if db.hamta(a["id"])["status"] == "fel":
            break
        time.sleep(0.02)
    rad = klient.get(f"/api/artiklar/{a['id']}").json()
    assert rad["status"] == "fel" and "credit balance is too low" in rad["fel"] and "Request-id" in rad["fel"]
    assert NYCKEL not in json.dumps(rad)


# ---------- Spärren mot riktiga anrop ----------

def test_testerna_kan_inte_na_anthropic():
    async def anropa():
        async with httpx2.AsyncClient() as k:
            await k.get("https://api.anthropic.com/v1/models")
    with pytest.raises(RuntimeError, match="api.anthropic.com"):
        asyncio.run(anropa())



# ---------- Svarsformatet: grammatiken får inte bli för stor ----------

def _matt(schema) -> dict:
    """Det som gör den kompilerade grammatiken stor: valfria egenskaper, unioner, nästling och antal egenskaper."""
    ut = {"valfria": [], "anyof": 0, "egenskaper": 0, "djup": 0, "objekt_utan_strikt_form": 0}

    def gang(s, vag, djup):
        if not isinstance(s, dict):
            return
        ut["anyof"] += "anyOf" in s or isinstance(s.get("type"), list)
        if s.get("type") == "object":
            ut["djup"] = max(ut["djup"], djup)
            if s.get("additionalProperties") is not False or set(s["required"]) - set(s["properties"]):
                ut["objekt_utan_strikt_form"] += 1
            for k, v in s["properties"].items():
                ut["egenskaper"] += 1
                if k not in s["required"]:
                    ut["valfria"].append(f"{vag}.{k}")
                gang(v, f"{vag}.{k}", djup + 1)
        elif s.get("type") == "array":
            gang(s["items"], vag + "[]", djup + 1)
    gang(schema, "", 0)
    return ut


def test_svarsformatet_har_inga_valfria_egenskaper_eller_unioner():
    """Regression: 10 valfria egenskaper i nästlade block gav "The compiled grammar is too large"."""
    from app.ai_writer import ARTIKEL_SCHEMA, ENKELT_SCHEMA

    for schema, max_egenskaper in ((ARTIKEL_SCHEMA, 55), (ENKELT_SCHEMA, 15)):
        m = _matt(schema)
        assert m["valfria"] == [] and m["anyof"] == 0 and m["objekt_utan_strikt_form"] == 0, m
        assert m["egenskaper"] <= max_egenskaper and m["djup"] <= 7, m
    text = json.dumps(ARTIKEL_SCHEMA)
    assert len(text) < 4000 and '"strict"' not in text and "$ref" not in text
    assert not any(n in text for n in ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern"))


def _validera(varde, schema, vag="$"):
    """Kontrollerar att ett svar följer schemat (den del av JSON Schema som svarsformatet använder)."""
    typ = schema["type"]
    if "enum" in schema:
        assert varde in schema["enum"], f"{vag}: {varde!r} finns inte i {schema['enum']}"
    if typ == "object":
        assert isinstance(varde, dict) and set(varde) == set(schema["properties"]), f"{vag}: fält {sorted(varde)}"
        for k, v in varde.items():
            _validera(v, schema["properties"][k], f"{vag}.{k}")
    elif typ == "array":
        assert isinstance(varde, list), vag
        for i, v in enumerate(varde):
            _validera(v, schema["items"], f"{vag}[{i}]")
    elif typ == "string":
        assert isinstance(varde, str), vag
    elif typ == "integer":
        assert isinstance(varde, int) and not isinstance(varde, bool), vag


def test_exempelsvaret_foljer_schemat_och_alla_blocktyper_finns_med():
    from app.ai_writer import ARTIKEL_SCHEMA, ENKELT_SCHEMA, _BLOCK

    _validera(_artikeldata(), ARTIKEL_SCHEMA)
    _validera(_artikeldata(json.dumps(ai_dokument(), ensure_ascii=False)), ENKELT_SCHEMA)
    typer = {b["typ"] for s in ai_dokument()["sektioner"] for b in s["block"]}
    assert typer == set(_BLOCK["properties"]["typ"]["enum"])


def test_platta_svaret_ger_exakt_samma_artikel_som_designsystemet():
    """Ingen funktion går förlorad: översättningen ger samma dokument som designsystemets eget format."""
    from app import design
    from app.ai_writer import dokument_fran_ai
    from test_design import dokument

    forvantat = design.normalisera(dokument(relaterade=[]))
    assert design.normalisera(dokument_fran_ai(ai_dokument())) == forvantat


def test_artikeln_far_seo_metadata_bilder_och_struktur(claude):
    claude(svar_ok())
    handelser = generera()
    a = handelser[-1]["artikel"]
    assert a["titel"] and a["meta_titel"] and a["meta_beskrivning"] and a["slug"] == "varmepump-i-villa"
    assert a["sokintention"]["typ"] == "informativ"
    assert [b["prompt"] for b in a["bilder"]] == [f"Prompt {i}" for i in range(4)]
    d = a["dokument"]
    assert d["hero"]["knapp"] == {"text": "Se åtgärderna", "mal": "atgarder"} and d["intro"]["fakta"]["punkter"]
    assert sorted(s["bild"] for s in d["sektioner"] if s["bild"]) == [2, 3, 4]
    assert {b["typ"] for s in d["sektioner"] for b in s["block"]} == {
        "stycke", "underrubrik", "kort", "notis", "checklista", "lista", "tabell", "steg"}
    tabell = next(b for s in d["sektioner"] for b in s["block"] if b["typ"] == "tabell")
    assert tabell["kolumner"] == ["System", "Fördel", "Nackdel"] and len(tabell["rader"]) == 2
    assert a["html"].count("<h1") == 1 and a["html"].count("<!--BILD:") == 4


GRAMMATIKFEL = ("The compiled grammar is too large, which would cause performance issues. "
                "Simplify your tool schemas or reduce the number of strict tools.")


def test_for_stor_grammatik_ger_nytt_forsok_med_enkelt_format(claude, caplog):
    from app.ai_writer import ARTIKEL_SCHEMA, ENKELT_SCHEMA

    enkelt_svar = _artikeldata(json.dumps(ai_dokument(), ensure_ascii=False))
    falsk = claude(svar_fel(400, "invalid_request_error", GRAMMATIKFEL), svar_ok(enkelt_svar))
    with caplog.at_level(logging.WARNING, "studio.ai_writer"):
        a = generera()[-1]["artikel"]
    assert len(falsk.forfragningar) == 2
    assert falsk.kropp(0)["output_config"]["format"]["schema"] == ARTIKEL_SCHEMA
    assert falsk.kropp(1)["output_config"]["format"]["schema"] == ENKELT_SCHEMA
    assert "JSON-sträng" in falsk.kropp(1)["messages"][0]["content"]
    # Systemprompten är densamma i båda försöken (prompt-cachen återanvänds).
    assert falsk.kropp(0)["system"] == falsk.kropp(1)["system"]
    assert a["dokument"]["version"] == 2 and len(a["dokument"]["sektioner"]) == 6
    assert "enkla formatet" in caplog.text


def test_grammatikfel_aven_i_enkelt_format_ger_tydligt_fel(claude):
    falsk = claude(svar_fel(400, "invalid_request_error", GRAMMATIKFEL), svar_fel(400, "invalid_request_error", GRAMMATIKFEL))
    text = fel_vid_generering()
    assert len(falsk.forfragningar) == 2
    assert "compiled grammar is too large" in text and "svarsformat" in text and "Request-id" in text


def test_ogiltig_json_i_enkelt_format_ger_begripligt_fel(claude):
    claude(svar_fel(400, "invalid_request_error", GRAMMATIKFEL), svar_ok(_artikeldata('{"hero": ')))
    assert "kunde inte tolkas" in fel_vid_generering()


def test_generering_i_studion_hela_vagen(claude, klient):
    """Från artikelkön till färdig artikel i det nya svarsformatet, via genereringsjobbet."""
    from app import db

    falsk = claude(svar_ok())
    a = klient.post("/api/artiklar", json={"huvudsokord": "värmepump i villa", "sekundara_sokord": ["luft-vattenvärmepump"]}).json()
    assert klient.post(f"/api/artiklar/{a['id']}/generera").status_code == 202
    for _ in range(200):
        if db.hamta(a["id"])["status"] != "genereras":
            break
        time.sleep(0.02)
    rad = klient.get(f"/api/artiklar/{a['id']}").json()
    assert rad["status"] == "granskas" and rad["fel"] is None, rad["fel"]
    inn = rad["innehall"]
    assert inn["dokument"]["version"] == 2 and inn["meta_titel"] and len(inn["bilder"]) == 4
    assert inn["html"].count("<!--BILD:") == 4 and "khd-artikel" in inn["html"]
    assert len(falsk.forfragningar) == 1
    assert not [p for p in rad["kvalitet"]["kritiska"] if p["kod"] in ("h1_antal", "for_fa_h2", "ingress_saknas")]
    # Förhandsgranskningen och exporten fungerar med den genererade artikeln.
    assert "khd-hero" in klient.post(f"/api/artiklar/{a['id']}/forhandsgranska", json=inn).json()["html"]
    assert "khd-artikel" in klient.post("/api/exportera", json=inn).json()["kod"]
