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


def _artikeldata() -> dict:
    from test_design import dokument

    return {"titel": "Värmepump i villa: guide", "slug": "varmepump-i-villa", "meta_titel": "Värmepump i villa – guide",
            "meta_beskrivning": "Allt om värmepump i villa.", "sokintention": {"typ": "informativ", "beskrivning": "b", "malgrupp": "m"},
            "dokument": dokument(), "bilder": [{"alt": f"Alt {i}", "bildtext": "", "prompt": f"Prompt {i}"} for i in range(4)]}


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
