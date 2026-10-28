"""Workflows och hjälpskriptet för Fly.io. flyctl är alltid ett låtsas-program; inget anrop når Fly.io."""

import json
import re
import sys
from pathlib import Path

import pytest

ROT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROT / "scripts"))

import fly_github  # noqa: E402

HEMLIGT = {"STUDIO_USER": "moa", "STUDIO_PASSWORD": "ett-langt-hemligt-losenord", "OPENAI_API_KEY": "sk-proj-hemlig-123",
           "ANTHROPIC_API_KEY": "sk-ant-hemlig-456", "WP_USER": "ai-redaktor-x", "WP_APP_PASSWORD": "wp-hemligt-789"}


class FalskFly:
    def __init__(self, app_finns=False, volym=False, hemligheter=(), maskiner=0, stod_retention=True):
        self.appar = ["klimathemmet-studio"] if app_finns else []
        self.volymer = [{"name": "studio_data", "state": "created"}] if volym else []
        self.hemligheter = {n: "befintlig" for n in hemligheter}
        self.maskiner = [{"state": "started"}] * maskiner
        self.stod_retention = stod_retention
        self.anrop: list[list[str]] = []
        self.indata: list[str] = []
        self.loggar = "Start: miljö produktion, publiceringsläge simulering, inloggning på"

    def kor(self, args, indata=None):
        self.anrop.append(args)
        if indata:
            self.indata.append(indata)
        a = [x for x in args if x != "--json"]
        if a[:2] == ["apps", "list"]:
            return json.dumps([{"Name": n} for n in self.appar])
        if a[:2] == ["apps", "create"]:
            self.appar.append(a[2])
            return ""
        if a[:2] == ["volumes", "list"]:
            return json.dumps(self.volymer)
        if a[:2] == ["volumes", "create"]:
            if "--snapshot-retention" in a and not self.stod_retention:
                raise fly_github.Fel("flyctl volumes create misslyckades: unknown flag: --snapshot-retention")
            self.volymer.append({"name": a[2], "state": "created"})
            return ""
        if a[:2] == ["secrets", "list"]:
            return json.dumps([{"Name": n} for n in self.hemligheter])
        if a[:2] == ["secrets", "import"]:
            for rad in indata.splitlines():
                namn, varde = rad.split("=", 1)
                self.hemligheter[namn] = varde
            return ""
        if a[:2] == ["secrets", "unset"]:
            self.hemligheter.pop(a[2], None)
            return ""
        if a[:2] == ["machine", "list"]:
            return json.dumps(self.maskiner)
        if a[:1] == ["logs"]:
            return self.loggar
        raise AssertionError(f"oväntat flyctl-anrop: {args}")


@pytest.fixture()
def fly(monkeypatch):
    for namn in HEMLIGT:
        monkeypatch.delenv(namn, raising=False)
    monkeypatch.delenv("FLY_APP", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    def skapa(**kw):
        f = FalskFly(**kw)
        monkeypatch.setattr(fly_github, "kor", f.kor)
        return f
    return skapa


def satt_hemligheter(monkeypatch, **extra):
    for k, v in (HEMLIGT | extra).items():
        monkeypatch.setenv(k, v)


def test_forbered_skapar_app_volym_och_hemligheter_utan_att_visa_varden(fly, monkeypatch, capsys):
    f = fly()
    satt_hemligheter(monkeypatch)
    fly_github.forbered("personal")
    assert f.appar == ["klimathemmet-studio"]
    skapa_volym = next(a for a in f.anrop if a[:2] == ["volumes", "create"])
    assert skapa_volym[2:9] == ["studio_data", "--region", "arn", "--size", "1", "--yes", "-a"]
    assert "--snapshot-retention" in skapa_volym
    # Värdena går via stdin och hamnar aldrig bland argumenten eller i utskriften.
    assert all(v not in " ".join(sum(f.anrop, [])) for v in HEMLIGT.values())
    assert ["secrets", "import", "--stage", "-a", "klimathemmet-studio"] in f.anrop
    ut = capsys.readouterr()
    assert all(v not in ut.out + ut.err for v in HEMLIGT.values())
    assert "STUDIO_SESSION_SECRET" in ut.out and "OPENAI_API_KEY" in ut.out
    assert len(f.hemligheter["STUDIO_SESSION_SECRET"]) >= 60
    assert f.hemligheter["OPENAI_API_KEY"] == HEMLIGT["OPENAI_API_KEY"]
    # Inget driftsätts och ingen maskin startas.
    assert not any(a[0] in ("deploy", "machine") and a[1:2] != ["list"] for a in f.anrop)


def test_forbered_igen_skapar_inget_nytt_och_behaller_sessionsnyckeln(fly, monkeypatch):
    f = fly(app_finns=True, volym=True, hemligheter=("STUDIO_USER", "STUDIO_PASSWORD", "STUDIO_SESSION_SECRET"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-ny")
    fly_github.forbered("personal")
    assert not any(a[:2] in (["apps", "create"], ["volumes", "create"]) for a in f.anrop)
    assert f.hemligheter["STUDIO_SESSION_SECRET"] == "befintlig" and f.hemligheter["OPENAI_API_KEY"] == "sk-proj-ny"


def test_forbered_kraver_inloggning_och_langt_losenord(fly, monkeypatch):
    fly(app_finns=True, volym=True)
    with pytest.raises(fly_github.Fel, match="STUDIO_USER, STUDIO_PASSWORD"):
        fly_github.forbered("personal")
    satt_hemligheter(monkeypatch, STUDIO_PASSWORD="kort")
    with pytest.raises(fly_github.Fel, match="minst 12 tecken"):
        fly_github.forbered("personal")


def test_publiceringslaget_kan_aldrig_vara_en_hemlighet(fly, monkeypatch):
    f = fly(app_finns=True, volym=True, hemligheter=("STUDIO_USER", "STUDIO_PASSWORD", "STUDIO_SESSION_SECRET", "STUDIO_PUBLICERING"))
    with pytest.raises(fly_github.Fel, match="STUDIO_PUBLICERING får inte"):
        fly_github.fore()
    fly_github.forbered("personal")
    assert "STUDIO_PUBLICERING" not in f.hemligheter
    # Skriptet skickar aldrig själv några lägen till Fly.io.
    monkeypatch.setenv("STUDIO_PUBLICERING", "skarp")
    fly_github.forbered("personal")
    assert "STUDIO_PUBLICERING" not in f.hemligheter


def test_volym_skapas_aven_om_flaggan_for_ogonblicksbilder_saknas(fly, monkeypatch):
    f = fly(stod_retention=False)
    satt_hemligheter(monkeypatch)
    fly_github.forbered("personal")
    assert f.volymer and sum(a[:2] == ["volumes", "create"] for a in f.anrop) == 2


def test_fore_godkanner_bara_ett_sakert_lage(fly, monkeypatch):
    kravda = ("STUDIO_USER", "STUDIO_PASSWORD", "STUDIO_SESSION_SECRET")
    fly(app_finns=True, volym=True, hemligheter=kravda, maskiner=1)
    fly_github.fore()
    fly(app_finns=True, volym=False, hemligheter=kravda)
    with pytest.raises(fly_github.Fel, match="Volymen studio_data saknas"):
        fly_github.fore()
    fly(app_finns=False)
    with pytest.raises(fly_github.Fel, match="finns inte på Fly.io"):
        fly_github.fore()
    fly(app_finns=True, volym=True, hemligheter=("STUDIO_USER",))
    with pytest.raises(fly_github.Fel, match="STUDIO_PASSWORD, STUDIO_SESSION_SECRET"):
        fly_github.fore()
    fly(app_finns=True, volym=True, hemligheter=kravda, maskiner=2)
    with pytest.raises(fly_github.Fel, match="fler än en maskin"):
        fly_github.fore()
    original = fly_github.fly_toml()
    monkeypatch.setattr(fly_github, "fly_toml", lambda: original | {"env": original["env"] | {"STUDIO_PUBLICERING": "skarp"}})
    fly(app_finns=True, volym=True, hemligheter=kravda, maskiner=1)
    with pytest.raises(fly_github.Fel, match="inte simulering"):
        fly_github.fore()


def test_efter_kontrollerar_https_inloggning_en_maskin_och_simulering(fly, monkeypatch):
    svar = {"https://klimathemmet-studio.fly.dev/healthz": (200, '{"ok":true}', ""),
            "https://klimathemmet-studio.fly.dev/api/artiklar": (401, "", ""),
            "http://klimathemmet-studio.fly.dev/login": (301, "", "https://klimathemmet-studio.fly.dev/login")}
    monkeypatch.setattr(fly_github, "_hamta", lambda url: svar[url])
    monkeypatch.setattr(fly_github.time, "sleep", lambda s: None)
    f = fly(maskiner=1)
    fly_github.efter(forsok=2)
    svar["https://klimathemmet-studio.fly.dev/api/artiklar"] = (200, "[]", "")
    with pytest.raises(fly_github.Fel, match="förväntat 401"):
        fly_github.efter(forsok=2)
    svar["https://klimathemmet-studio.fly.dev/api/artiklar"] = (401, "", "")
    f.loggar = "Start: miljö produktion, publiceringsläge skarp"
    with pytest.raises(fly_github.Fel, match="publiceringsläge simulering"):
        fly_github.efter(forsok=2)


def test_appnamnet_kan_anges_som_github_variabel(monkeypatch):
    monkeypatch.delenv("FLY_APP", raising=False)
    assert fly_github.appnamn() == fly_github.fly_toml()["app"]
    monkeypatch.setenv("FLY_APP", "klimathemmet-studio-moa")
    assert fly_github.appnamn() == "klimathemmet-studio-moa"


# ---------- Själva workflow-filerna ----------

def _workflow(namn):
    import yaml

    text = (ROT / ".github" / "workflows" / namn).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return text, data, data.get(True, data.get("on"))


def test_driftsattning_startas_bara_manuellt_och_efter_testerna():
    text, data, on = _workflow("fly-driftsatt.yml")
    assert list(on) == ["workflow_dispatch"]
    jobb = data["jobs"]
    assert jobb["tester"]["uses"] == "./.github/workflows/tester.yml" and jobb["tester"]["needs"] == "bekraftelse"
    assert jobb["driftsatt"]["needs"] == "tester"
    steg = " ".join(s.get("run", "") for s in jobb["driftsatt"]["steps"])
    assert steg.index("fly_github.py fore") < steg.index("flyctl deploy") < steg.index("fly_github.py efter")
    assert "--ha=false" in steg and "--local-only" in steg
    assert data["permissions"] == {"contents": "read"}
    _, forbered, on_f = _workflow("fly-forbered.yml")
    assert list(on_f) == ["workflow_dispatch"] and "deploy" not in json.dumps(forbered)


def test_workflows_innehaller_inga_hemligheter_och_skriver_inte_ut_dem():
    for f in (ROT / ".github" / "workflows").glob("*.yml"):
        text = f.read_text(encoding="utf-8")
        assert "set -x" not in text
        assert not re.search(r"echo[^\n]*\$\{?(FLY_API_TOKEN|[A-Z_]*PASSWORD|[A-Z_]*API_KEY)", text), f.name
        # Alla känsliga värden kommer från GitHub Secrets.
        for rad in re.findall(r"^\s*(FLY_API_TOKEN|STUDIO_PASSWORD|OPENAI_API_KEY|ANTHROPIC_API_KEY|WP_APP_PASSWORD):\s*(.+)$", text, re.M):
            assert re.fullmatch(r"\$\{\{ secrets\.[A-Z_]+ \}\}", rad[1].strip()), (f.name, rad[0])
        assert "STUDIO_PUBLICERING" not in text
    _, tester, on = _workflow("tester.yml")
    assert "secrets" not in json.dumps(tester)
