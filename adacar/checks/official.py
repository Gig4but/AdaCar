"""Official registers for the dealer card (PLAN §2.4, ported from AdaWork's employer checks):
ARES (company register) by IČO in batches of 100 and by name for the resolver, ISIR
(insolvency proceedings) only when ARES flags one, and the Ministry of Finance's list of
unreliable VAT payers, downloaded daily and looked up locally.
"""

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx

from adacar.net.http import Refresh
from adacar.normalize.text import fold

ARES = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
BATCH = 100  # IČOs per ARES call
ARES_GAP = 0.3  # seconds between ARES calls (MF may block above 500 a minute)
ISIR_URL = "https://isir.justice.cz:8443/isir_cuzk_ws/IsirWsCuzkService"
ISIR_NS = "http://isirws.cca.cz/types/"
VAT_URL = "https://adisrws.mfcr.cz/dpr/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP"
VAT_NS = "http://adis.mfcr.cz/rozhraniCRPDPH/"
VAT_FILE = "nespolehlivi-platci.json"
# Legal forms (ARES "PravniForma") that sell no cars: trade unions, foundations, owners'
# associations.
NOT_SELLERS = frozenset({"707", "117", "118", "145", "706", "751"})
# Natural persons in business (sole traders, freelancers): the app keeps no data on persons.
SOLE_TRADERS = frozenset({"101", "102", "105", "106", "107", "108", "424", "425"})


class TooMany(Exception):
    """ARES found more than 1000 subjects for the name."""


@dataclass
class Company:
    ico: str
    name: str
    legal_form: str = ""
    founded: date | None = None
    ended: date | None = None
    address: str = ""
    municipality_code: str | None = None
    dic: str | None = None
    registrations: dict[str, str] = field(default_factory=dict)
    nace: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.ended is None

    @property
    def insolvency_flag(self) -> bool:
        """ARES says the insolvency register has a record (ISIR then tells which)."""
        return self.registrations.get("stavZdrojeIr", "NEEXISTUJICI") != "NEEXISTUJICI"

    @property
    def sells_cars(self) -> bool:
        """Registered for the car trade or its repairs (CZ-NACE 45)."""
        return any(n.startswith("45") for n in self.nace)


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def company(record: dict) -> Company:
    """An ARES record; `ico` is "" for one without a Czech IČO (some foreign subjects)."""
    seat = record.get("sidlo") or {}
    ico = record.get("ico") or ""
    code = seat.get("kodObce")
    return Company(
        ico=ico if re.fullmatch(r"\d{8}", ico) else "",
        name=record.get("obchodniJmeno") or "",
        legal_form=record.get("pravniForma") or "",
        founded=_date(record.get("datumVzniku")),
        ended=_date(record.get("datumZaniku")),
        address=seat.get("textovaAdresa") or "",
        municipality_code=str(code) if code else None,
        dic=record.get("dic"),
        registrations=record.get("seznamRegistraci") or {},
        nace=list(record.get("czNace") or record.get("czNace2008") or []),
    )


class Ares:
    # The gap between calls is kept across instances: the background pass and opened cards
    # each make their own, and ARES counts them all.
    _last = 0.0
    _turn = threading.Lock()

    def __init__(self, client: httpx.Client, sleep=time.sleep, clock=time.monotonic) -> None:
        self.client = client
        self.sleep, self.clock = sleep, clock

    def _post(self, path: str, body: dict) -> dict:
        with Ares._turn:
            wait = Ares._last + ARES_GAP - self.clock()
            if wait > 0:
                self.sleep(wait)
            Ares._last = self.clock()
        resp = self.client.post(
            ARES + path, json=body, headers={"Accept": "application/json"}, timeout=60
        )
        if resp.status_code == 400 and "PRILIS_MNOHO" in resp.text:
            raise TooMany(body.get("obchodniJmeno"))
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def companies(self, icos: list[str]) -> dict[str, Company]:
        """ARES records by IČO (missing IČOs are left out)."""
        found: dict[str, Company] = {}
        for i in range(0, len(icos), BATCH):
            body = {"ico": icos[i : i + BATCH], "start": 0, "pocet": BATCH}
            for record in self._post("/ekonomicke-subjekty/vyhledat", body).get(
                "ekonomickeSubjekty", []
            ):
                c = company(record)
                if c.ico:
                    found[c.ico] = c
        return found

    def search(self, name: str, limit: int = 200) -> tuple[int, list[Company]]:
        """(how many subjects ARES has under the name, the first `limit`). ARES matches all
        words, ignores case and diacritics, and orders by IČO, not by relevance."""
        body = {"obchodniJmeno": name, "start": 0, "pocet": limit}
        data = self._post("/ekonomicke-subjekty/vyhledat", body)
        records = data.get("ekonomickeSubjekty") or []
        found = [c for c in map(company, records) if c.ico]  # foreign ones may have no IČO
        return data.get("pocetCelkem", len(records)), found


# --- ISIR -------------------------------------------------------------------------------------

_ISIR_ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    f'xmlns:typ="{ISIR_NS}"><soapenv:Body><typ:getIsirWsCuzkDataRequest>'
    "<ic>{ico}</ic><maxRelevanceVysledku>2</maxRelevanceVysledku>"
    "</typ:getIsirWsCuzkDataRequest></soapenv:Body></soapenv:Envelope>"
)
# ISIR's state codes ("VYRIZENA", "K-PO ZRUSENI") by their start, as plain-text keys.
_ISIR_STATES = (
    ("nevyrizen", "pending"),
    ("moratorium", "moratorium"),
    ("upadek", "bankrupt"),
    ("konkurs", "konkurs"),
    ("oddluzeni", "debt_relief"),
    ("reorganiz", "reorganization"),
    ("vyrizen", "ended"),
    ("pravomocn", "ended"),
    ("odskrtnut", "ended"),
    ("zrusen", "cancelled"),
    ("k-po zrusen", "cancelled"),
    ("mylny", "error"),
)
ENDED_STATES = ("vyrizen", "pravomocn", "odskrtnut", "zrusen", "mylny")  # no warning


def insolvency_state(state: str) -> str | None:
    """The i18n key ("dealer.isir.<key>") of an ISIR state code; None for an unknown one."""
    folded = fold(state).strip()
    return next((key for start, key in _ISIR_STATES if folded.startswith(start)), None)


def insolvency(client: httpx.Client, ico: str) -> list[dict]:
    """Insolvency proceedings of the IČO: [{"state", "court", "case", "since", "until", "url"}].
    Empty when there are none."""
    resp = client.post(
        ISIR_URL,
        content=_ISIR_ENVELOPE.format(ico=ico).encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        timeout=60,
    )
    resp.raise_for_status()
    try:
        return parse_insolvency(resp.content)
    except ET.ParseError as exc:  # a SyntaxError: callers handle ValueError
        raise ValueError(f"ISIR answer for {ico} is not XML: {exc}") from exc


def parse_insolvency(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    found = []
    for data in root.iter("data"):

        def get(tag: str, data=data) -> str:
            return (data.findtext(tag) or "").strip()

        case = f"{get('druhVec')} {get('bcVec')}/{get('rocnik')}".strip()
        found.append(
            {
                "state": get("druhStavKonkursu"),
                "court": get("nazevOrganizace"),
                "case": case if case != "/" else "",
                "since": get("datumPmZahajeniUpadku").rstrip("Z") or None,
                "until": get("datumPmUkonceniUpadku").rstrip("Z") or None,
                "url": get("urlDetailRizeni"),
            }
        )
    return found


# --- unreliable VAT payers --------------------------------------------------------------------


def unreliable_vat_payers(client: httpx.Client, directory: Path) -> int:
    """Downloads the list of unreliable VAT payers (~0.5 MB); returns how many there are."""
    body = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:roz="{VAT_NS}"><soapenv:Body><roz:SeznamNespolehlivyPlatceRequest/>'
        "</soapenv:Body></soapenv:Envelope>"
    )
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": VAT_NS + "getSeznamNespolehlivyPlatce",
    }
    resp = client.post(VAT_URL, content=body.encode(), headers=headers, timeout=120)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    status = root.find(f".//{{{VAT_NS}}}status")
    # statusCode 2 during the daily outage (0:00–0:10): tried again later.
    if status is None or status.get("statusCode") != "0":
        text = status.get("statusText") if status is not None else "no status"
        raise ValueError(f"VAT register answered: {text}")
    payers = {
        e.get("dic"): e.get("datumZverejneniNespolehlivosti")
        for e in root.iter(f"{{{VAT_NS}}}statusPlatceDPH")
        if e.get("dic") and e.get("nespolehlivyPlatce") == "ANO"
    }
    if not payers:
        raise ValueError("VAT register sent an empty list")
    data = {"generated": status.get("odpovedGenerovana"), "payers": payers}
    path = directory / VAT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    os.replace(tmp, path)
    return len(payers)


def refreshes(directory: Path) -> tuple[Refresh, ...]:
    return (Refresh("vat", timedelta(days=1), lambda c: unreliable_vat_payers(c, directory)),)


class VatList:
    """The daily VAT list from the cache folder, reloaded when it changes."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / VAT_FILE
        self.stamp = 0.0
        self.payers: dict[str, str] = {}  # DIČ without "CZ" -> published unreliable since

    def fresh(self) -> "VatList":
        stamp = self.path.stat().st_mtime if self.path.exists() else 0.0
        if stamp != self.stamp:
            if stamp:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.payers = {k.removeprefix("CZ"): v for k, v in data.get("payers", {}).items()}
            self.stamp = stamp  # after loading: a file that failed to load is tried again
        return self

    def unreliable_since(self, dic: str | None) -> str | None:
        """When the VAT payer was published as unreliable; None when it is not."""
        return self.payers.get(dic.removeprefix("CZ")) if dic else None
