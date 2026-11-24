"""Vehicle and seller checks (PLAN §4.5, M4): the VIN, the register, the mileage history,
dealers in ARES, ISIR and the VAT list, and the background pass."""

import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from adacar.checks import official, register, resolver, vehicle, vin
from adacar.checks.names import name_key, variants
from adacar.checks.service import GOOGLE_KEY, REGISTER_KEY, CheckService
from adacar.config import CheckSettings
from adacar.core import ParsedCar, ParsedLocation
from adacar.db.models import Car, Dealer, VehicleCheck
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.scraper.store import CarStore
from adacar.secrets import MemorySecrets

OCTAVIA_VIN = "TMBJJ7NE8J0123456"  # Škoda, model year 2018 (J)
NOW = datetime(2026, 11, 18, 12, 0)

# The register's answer, as its documentation shows it (shortened).
RECORD = {
    "DatumPrvniRegistrace": "2016-03-21T00:00:00",
    "DatumPrvniRegistraceVCr": "2019-06-02T00:00:00",
    "TovarniZnacka": "ŠKODA",
    "ObchodniOznaceni": "OCTAVIA",
    "MotorMaxVykon": "110 / 4000",
    "Palivo": "NM",
    "VozidloKaroserieBarva": "ŠEDÁ",
    "OrvZadrzeno": False,
    "RzZadrzena": False,
    "PravidelnaTechnickaProhlidkaDo": "2027-06-01T00:00:00",
    "StatusNazev": "PROVOZOVANÉ",
    "PocetVlastniku": 3,
    "PocetProvozovatelu": 3,
}


@pytest.fixture(autouse=True)
def no_gaps(monkeypatch):
    """The registers' pauses between calls are for the real services."""
    monkeypatch.setattr(register, "GAP", 0)
    monkeypatch.setattr(official, "ARES_GAP", 0)


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_sessionmaker(engine)


def add_car(sessions, external_id, source="sauto", **fields) -> int:
    fields.setdefault("title", f"Auto {external_id}")
    with sessions() as s:
        store = CarStore(s, source)
        store.add(ParsedCar(external_id=external_id, **fields))
        store.commit()
        return s.scalars(select(Car.id).order_by(Car.id.desc())).first()


class Sites:
    """Fake ARES, ISIR, register and Google: answers by URL, calls recorded."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.ares: dict[str, dict] = {}  # IČO -> ARES record
        self.isir: dict[str, str] = {}  # IČO -> state
        self.vins: dict[str, dict] = {}  # VIN -> register record
        self.register_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url.startswith(register.API):
            if self.register_status != 200:
                return httpx.Response(self.register_status, text="Pro Váš klíč ...")
            data = self.vins.get(request.url.params["vin"])
            return httpx.Response(200, json={"Status": 1 if data else 0, "Data": data})
        if url.startswith(official.ARES):
            body = json.loads(request.content)
            if "ico" in body:
                found = [self.ares[i] for i in body["ico"] if i in self.ares]
            else:
                words = body["obchodniJmeno"].lower().split()
                found = [
                    r for r in self.ares.values()
                    if all(w in r["obchodniJmeno"].lower() for w in words)
                ]  # fmt: skip
            answer = {"pocetCelkem": len(found), "ekonomickeSubjekty": found}
            return httpx.Response(200, json=answer)
        if url.startswith(official.ISIR_URL):
            ico = request.content.decode().split("<ic>")[1].split("</ic>")[0]
            state = self.isir.get(ico, "")
            data = f"<data><druhStavKonkursu>{state}</druhStavKonkursu></data>" if state else ""
            return httpx.Response(200, content=f"<r>{data}</r>".encode())
        if "googleapis" in url:
            if url.endswith("searchText"):
                return httpx.Response(200, json={"places": [{"id": "p1"}]})
            return httpx.Response(200, json={
                "displayName": {"text": "Auto Plus"}, "rating": 4.4, "userRatingCount": 120,
                "googleMapsUri": "https://maps.google.com/?cid=1",
                "reviews": [{"rating": 5, "text": {"text": "Super"},
                             "relativePublishTimeDescription": "před měsícem"}],
            })  # fmt: skip
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def ares_record(ico, name, **extra) -> dict:
    record = {
        "ico": ico, "obchodniJmeno": name, "pravniForma": "112", "datumVzniku": "2010-01-05",
        "sidlo": {"textovaAdresa": "Praha 4", "kodObce": 554782}, "dic": f"CZ{ico}",
        "seznamRegistraci": {}, "czNace": ["45110"],
    }  # fmt: skip
    return record | extra


def make_service(sessions, tmp_path, sites, keys=None, profile=None, auto=True):
    settings = CheckSettings(auto=auto)
    service = CheckService(
        sessions, tmp_path / "lists", lambda: settings, MemorySecrets(keys or {}),
        profile_cars=lambda s: profile, client_factory=sites.client, clock=lambda: NOW,
    )  # fmt: skip
    return service


# --- the VIN -----------------------------------------------------------------------------------


def test_a_vin_tells_its_maker_and_for_the_vw_group_its_model_year():
    assert vin.clean(" tmbjj7ne8j0123456 ") == OCTAVIA_VIN
    assert vin.clean("TMBJJ7NE8J012345") is None  # 16 characters
    assert vin.clean("TMBJJ7NE8J01234O6") is None  # an O: never in a VIN
    decoded = vin.decode(OCTAVIA_VIN, 2018)
    assert decoded.makes == {"skoda"} and decoded.model_year == 2018
    assert vin.model_year("A", 1985) == 1980 and vin.model_year("A", 2012) == 2010
    assert vin.decode("WBA8E9C51GK123456", 2016).model_year is None  # BMW: not used
    assert vin.decode("XXX8E9C51GK123456", 2016).makes == frozenset()


def test_a_vin_of_another_maker_or_year_is_a_warning():
    assert vin.mismatches(OCTAVIA_VIN, "skoda", 2018) == []
    assert vin.mismatches(OCTAVIA_VIN, "skoda", 2019) == []  # registered the next year
    assert vin.mismatches(OCTAVIA_VIN, "skoda", 2017) == []  # model year 2018 from mid-2017
    assert vin.mismatches(OCTAVIA_VIN, "volkswagen", 2021) == ["vin_make", "vin_year"]
    assert vin.mismatches("VSSZZZKJZMR123456", "cupra", 2021) == []  # Seat's plant: both
    assert vin.mismatches(OCTAVIA_VIN, None, None) == []
    # The site kept the make of an ad rewritten for another car; the title names the right one.
    assert vin.mismatches(OCTAVIA_VIN, "jaguar", 2018, "Škoda Octavia 2.0 TDI") == []
    assert vin.mismatches("VF1HJD40669617908", "dacia", 2022) == []  # built by Renault
    assert vin.mismatches("KMHGN4JE9HU000000", "genesis", 2017) == []
    assert vin.mismatches("KMTG34TA0PU000000", "genesis", 2023) == []


# --- the register ------------------------------------------------------------------------------


def test_the_registers_record_is_compared_with_the_ad():
    facts = register.facts(RECORD)
    assert facts["power_kw"] == 110 and facts["first_registration"] == "2016-03-21"
    assert register.imported_on(facts) == "2019-06-02"
    honest = Car(title="Octavia", description="Dovoz z Německa", make="skoda", year=2016,
                 power_kw=110)  # fmt: skip
    assert register.mismatches(facts, honest) == []
    claims = Car(title="Octavia", description="Koupeno v ČR, 1. majitel", make="skoda",
                 year=2018, power_kw=135, first_owner=True)  # fmt: skip
    assert register.mismatches(facts, claims) == [
        "reg_year", "reg_power", "reg_imported", "reg_owners"
    ]  # fmt: skip
    not_claimed = Car(title="Octavia", description="Nekoupeno v ČR", make="skoda", year=2016)
    assert register.mismatches(facts, not_claimed) == []
    gone = register.facts(RECORD | {"StatusNazev": "ZANIKLÉ", "RzZadrzena": True})
    assert register.mismatches(gone, honest) == ["reg_seized", "reg_status"]
    resting = register.facts(RECORD | {"StatusNazev": "DOČASNĚ VYŘAZENÉ"})
    assert register.mismatches(resting, honest) == []  # a dealer's car off the road for sale
    vw = register.facts(RECORD | {"TovarniZnacka": "VOLKSWAGEN"})
    assert register.mismatches(vw, honest) == ["reg_make"]


def test_the_register_is_asked_with_the_users_key():
    sites = Sites()
    sites.vins[OCTAVIA_VIN] = RECORD
    with sites.client() as client:
        reg = register.Register(client, "k" * 20)
        assert reg.lookup(OCTAVIA_VIN)["make"] == "ŠKODA"
        assert reg.lookup("TMBJJ7NE8J0999999") is None
        sites.register_status = 401
        with pytest.raises(register.BadKey):
            reg.lookup(OCTAVIA_VIN)
        sites.register_status = 429
        with pytest.raises(register.TooFast):
            reg.lookup(OCTAVIA_VIN)


def test_calls_to_the_register_keep_their_distance(monkeypatch):
    monkeypatch.setattr(register, "GAP", 2.4)
    slept = []
    clock = iter([100.0, 100.0, 101.0, 102.4]).__next__
    sites = Sites()
    with sites.client() as client:
        reg = register.Register(client, "k" * 20, sleep=slept.append, clock=clock)
        register.Register._last = 0.0
        reg.lookup(OCTAVIA_VIN)
        reg.lookup(OCTAVIA_VIN)
    assert slept == [pytest.approx(1.4)]


# --- the mileage history -----------------------------------------------------------------------


def test_a_lower_km_than_seen_before_is_a_rollback():
    seen = vehicle.record_km([], 150_000, "2026-01-01")
    seen = vehicle.record_km(seen, 150_000, "2026-02-01")  # the same: not again
    seen = vehicle.record_km(seen, 155_000, "2026-03-01")
    assert [r["km"] for r in seen] == [150_000, 155_000] and not vehicle.rolled_back(seen)
    assert vehicle.rolled_back(vehicle.record_km(seen, 98_000, "2026-09-01"))
    assert not vehicle.rolled_back(vehicle.record_km(seen, 154_000, "2026-09-01"))  # a typo
    # Two ads of one VIN at once do not grow the history in turns.
    assert vehicle.record_km(vehicle.record_km(seen, 98_000, "x"), 155_000, "y") == (
        vehicle.record_km(seen, 98_000, "x")
    )


def test_the_same_car_offered_again_with_fewer_km_is_caught(sessions, tmp_path):
    service = make_service(sessions, tmp_path, Sites())
    changed = []
    service.on_changed = lambda: changed.append(True)
    first = add_car(sessions, "1", make="skoda", year=2018, mileage_km=182_000, vin=OCTAVIA_VIN)
    service.run_pass()
    with sessions() as s:
        assert s.get(Car, first).check_warnings == []
        s.get(Car, first).status = "expired"  # sold, gone
        s.commit()
    again = add_car(sessions, "2", source="bazos", make="skoda", year=2018,
                    mileage_km=96_000, vin=OCTAVIA_VIN.lower())  # fmt: skip
    service.run_pass()
    with sessions() as s:
        assert s.get(Car, again).check_warnings == ["km_rollback"]
        assert [r["km"] for r in s.get(VehicleCheck, OCTAVIA_VIN).km_seen] == [182_000, 96_000]
    assert changed == [True]  # the rules pass merges the new warning


def test_the_rules_pass_shows_the_check_warnings(sessions, tmp_path):
    from conftest import make_ai

    from adacar.config import Settings

    settings = Settings.load(tmp_path / "config.json")
    ai = make_ai(tmp_path, settings, sessions)
    car_id = add_car(sessions, "1", make="volkswagen", year=2018, vin=OCTAVIA_VIN)
    make_service(sessions, tmp_path, Sites()).update_vehicles()
    ai.enricher.run_rules()
    with sessions() as s:
        assert s.get(Car, car_id).warnings == ["vin_make"]


# --- the register in the pass ------------------------------------------------------------------


def test_the_register_is_asked_for_the_profiles_cars_only(sessions, tmp_path):
    sites = Sites()
    sites.vins[OCTAVIA_VIN] = RECORD
    mine = add_car(sessions, "1", make="skoda", year=2018, power_kw=110, vin=OCTAVIA_VIN,
                   description="Koupeno v ČR")  # fmt: skip
    add_car(sessions, "2", make="skoda", year=2018, vin="TMBJJ7NE8J0999999")
    service = make_service(sessions, tmp_path, sites, {REGISTER_KEY: "k" * 20}, profile={mine})
    service.run_pass()
    asked = [c for c in sites.calls if c.startswith(register.API)]
    assert asked == [f"{register.API}?vin={OCTAVIA_VIN}"]
    with sessions() as s:
        record = s.get(VehicleCheck, OCTAVIA_VIN)
        assert record.register_status == "ok" and record.register["owners"] == 3
        assert s.get(Car, mine).check_warnings == ["reg_year", "reg_imported"]
    service.run_pass()  # read: not asked again for 30 days
    assert len([c for c in sites.calls if c.startswith(register.API)]) == 1


def test_no_key_no_register_and_a_refused_key_is_said(sessions, tmp_path):
    sites = Sites()
    mine = add_car(sessions, "1", make="skoda", vin=OCTAVIA_VIN)
    make_service(sessions, tmp_path, sites, profile={mine}).run_pass()
    assert not [c for c in sites.calls if c.startswith(register.API)]
    sites.register_status = 403
    service = make_service(sessions, tmp_path, sites, {REGISTER_KEY: "k" * 20}, profile={mine})
    service.run_pass()
    assert service.progress.message == "checks.register.bad_key"
    with sessions() as s:
        assert s.get(VehicleCheck, OCTAVIA_VIN).register_status is None  # asked again later


def test_an_unfiltered_profile_does_not_send_the_whole_market_to_the_register(sessions, tmp_path):
    sites = Sites()
    add_car(sessions, "1", make="skoda", vin=OCTAVIA_VIN)
    make_service(sessions, tmp_path, sites, {REGISTER_KEY: "k" * 20}, profile=None).run_pass()
    assert not [c for c in sites.calls if c.startswith(register.API)]


# --- dealers -------------------------------------------------------------------------------------


PLZEN = ParsedLocation(key="obec:554791", precision="municipality", municipality="Plzeň",
                       municipality_code="554791")  # fmt: skip


def test_names_become_keys_and_ares_queries():
    assert name_key("AUTO PLUS spol.s r.o.") == name_key("Auto Plus, spol. s r.o.") == "auto plus"
    plzen = frozenset({"plzen"})
    assert variants("AAA AUTO - Plzeň", plzen)[:2] == ["AAA AUTO - Plzeň", "AAA AUTO"]
    assert "Klokočka" in variants("Autosalon Klokočka")


class FakeAres:
    def __init__(self, companies, total=None):
        self.found = companies
        self.total = total
        self.queries = []

    def search(self, name, limit=200):
        self.queries.append(name)
        words = name.lower().split()
        hits = [c for c in self.found if all(w in c.name.lower() for w in words)]
        return (self.total or len(hits)), hits


def company(ico, name, **kw):
    return official.Company(ico=ico, name=name, **kw)


def test_a_branch_name_resolves_to_its_chain():
    ares = FakeAres([company("06898327", "AAA AUTO s.r.o.", municipality_code="554782")])
    res = resolver.resolve(ares, "AAA AUTO - Plzeň", "554791", frozenset({"plzen"}))
    assert res.ico == "06898327" and ares.queries == ["AAA AUTO - Plzeň", "AAA AUTO"]


def test_similar_companies_leave_the_choice_to_the_user():
    ares = FakeAres([
        company("05604095", "Autosalon Klokočka Holding s.r.o."),
        company("26435713", "Autosalon Klokočka Centrum s.r.o."),
        company("10840397", "Autosalon Klokočka Delta s.r.o."),
    ])  # fmt: skip
    res = resolver.resolve(ares, "Autosalon Klokočka Centrum Řepy")
    assert res.ico is None and res.method == "uncertain"  # "Řepy" is not in the name
    assert res.candidates[0].company.ico == "26435713"
    assert resolver.resolve(ares, "Autosalon Klokočka Centrum Řepy", "554782").method == "uncertain"
    praha = [company("26435713", "Autosalon Klokočka Centrum s.r.o.", municipality_code="554782")]
    assert resolver.resolve(FakeAres(praha), "Autosalon Klokočka Centrum Řepy", "554782").ico
    assert resolver.resolve(FakeAres([]), "Pan Novák").method == "none"


def test_a_dissolved_or_unrelated_company_is_not_sure():
    ares = FakeAres([company("1", "Auto Plus s.r.o.", ended=date(2020, 1, 1))])
    assert resolver.resolve(ares, "Auto Plus").ico is None


def dealer_setup(sessions, sites, name="Auto Plus s.r.o."):
    sites.ares["12345678"] = ares_record("12345678", "Auto Plus s.r.o.")
    car = add_car(sessions, "1", seller_kind="dealer", seller_name=name, location=PLZEN)
    with sessions() as s:
        return car, s.get(Car, car)


def test_a_dealers_cars_get_its_card_checked_in_the_registers(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    add_car(sessions, "2", seller_kind="dealer", seller_name="AUTO PLUS, s. r. o.")
    add_car(sessions, "3", seller_kind="private", seller_name="Jan Novák")
    service = make_service(sessions, tmp_path, sites, profile={car_id})
    service.run_pass()
    with sessions() as s:
        dealers = s.scalars(select(Dealer)).all()
        assert [d.name_key for d in dealers] == ["auto plus"]  # the private seller has none
        d = dealers[0]
        assert d.ico == "12345678" and d.match == "ares" and d.legal_name == "Auto Plus s.r.o."
        assert d.founded == date(2010, 1, 5) and d.checked_at == NOW and d.warnings == []
        cars = s.scalars(select(Car).order_by(Car.id)).all()
        assert [c.dealer_id for c in cars] == [d.id, d.id, None]
    service.run_pass()  # nothing is due: ARES not asked again
    assert len([c for c in sites.calls if c.startswith(official.ARES)]) == 2


def test_an_insolvent_or_unreliable_dealer_warns_on_its_cars(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    sites.ares["12345678"]["seznamRegistraci"] = {"stavZdrojeIr": "AKTIVNI"}
    sites.isir["12345678"] = "KONKURS"
    lists = tmp_path / "lists"
    lists.mkdir()
    (lists / official.VAT_FILE).write_text(json.dumps({"payers": {"12345678": "2025-05-01"}}))
    make_service(sessions, tmp_path, sites, profile={car_id}).run_pass()
    with sessions() as s:
        car = s.get(Car, car_id)
        assert car.dealer.warnings == ["insolvency", "vat"]
        assert car.all_warnings == ["dealer_insolvency", "dealer_vat"]
        assert car.dealer.vat_unreliable == "2025-05-01"


def test_the_user_picks_the_company(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites, name="Plus Cars")
    sites.ares["87654321"] = ares_record("87654321", "Úplně jiná s.r.o.")
    service = make_service(sessions, tmp_path, sites, profile={car_id})
    service.run_pass()
    with sessions() as s:
        did = s.get(Car, car_id).dealer_id
        assert s.get(Dealer, did).ico is None
    assert service.correct_dealer(did, "11111111") == "bad_ico"  # ARES does not know it
    assert service.correct_dealer(did, "abc") == "bad_ico"
    assert service.correct_dealer(did, "87654321") is None
    service.run_pass()  # the pass does not undo the user's choice
    with sessions() as s:
        d = s.get(Dealer, did)
        assert (d.ico, d.match, d.legal_name) == ("87654321", "user", "Úplně jiná s.r.o.")
        assert d.checked_at == NOW


def test_google_ratings_need_the_users_key(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    make_service(sessions, tmp_path, sites, profile={car_id}).run_pass()
    assert not [c for c in sites.calls if "googleapis" in c]
    service = make_service(sessions, tmp_path, sites, {GOOGLE_KEY: "g" * 20}, profile={car_id})
    service.run_pass()
    with sessions() as s:
        d = s.get(Car, car_id).dealer
        assert (d.rating, d.rating_count, d.reviews[0]["text"]) == (4.4, 120, "Super")
    assert service.dealers.google_quota().details == 1


def test_auto_off_asks_nothing_online_until_a_card_is_opened(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    service = make_service(sessions, tmp_path, sites, profile={car_id}, auto=False)
    service.run_pass()
    assert sites.calls == []
    with sessions() as s:
        did = s.get(Car, car_id).dealer_id
    service.check_dealer(did)
    with sessions() as s:
        assert s.get(Dealer, did).ico == "12345678"


def test_the_vat_list_is_downloaded_and_read():
    xml = (
        '<s:Envelope xmlns:s="x"><s:Body><r xmlns="http://adis.mfcr.cz/rozhraniCRPDPH/">'
        '<status statusCode="0" odpovedGenerovana="2026-11-18"/>'
        '<statusPlatceDPH dic="12345678" nespolehlivyPlatce="ANO" '
        'datumZverejneniNespolehlivosti="2025-05-01"/>'
        '<statusPlatceDPH dic="87654321" nespolehlivyPlatce="NE"/>'
        "</r></s:Body></s:Envelope>"
    )

    def handler(request):
        return httpx.Response(200, content=xml.encode())

    import tempfile
    from pathlib import Path

    folder = Path(tempfile.mkdtemp())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert official.unreliable_vat_payers(client, folder) == 1
    vat = official.VatList(folder).fresh()
    assert vat.unreliable_since("CZ12345678") == "2025-05-01"
    assert vat.unreliable_since("87654321") is None and vat.unreliable_since(None) is None


def test_isir_states_have_names():
    assert official.insolvency_state("KONKURS") == "konkurs"
    assert official.insolvency_state("VYRIZENA") == "ended"
    assert official.insolvency_state("NĚCO NOVÉHO") is None
    xml = (
        b"<r><data><druhStavKonkursu>KONKURS</druhStavKonkursu><bcVec>12</bcVec>"
        b"<rocnik>2024</rocnik><druhVec>INS</druhVec></data></r>"
    )
    assert official.parse_insolvency(xml)[0]["case"] == "INS 12/2024"


def test_a_stale_dealer_is_checked_again_after_30_days(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    make_service(sessions, tmp_path, sites, profile={car_id}).run_pass()
    with sessions() as s:
        d = s.get(Car, car_id).dealer
        d.checked_at = NOW - timedelta(days=31)
        s.commit()
    sites.ares["12345678"]["datumZaniku"] = "2026-10-01"
    make_service(sessions, tmp_path, sites, profile={car_id}).run_pass()
    with sessions() as s:
        assert s.get(Car, car_id).dealer.warnings == ["ended"]


# --- what the M4 review found ---------------------------------------------------------------


def test_the_checks_and_the_ai_do_not_write_over_each_other(sessions, tmp_path):
    """Check warnings have a column of their own: the AI's marks in `extra` stay."""
    car_id = add_car(sessions, "1", make="volkswagen", year=2018, vin=OCTAVIA_VIN)
    with sessions() as s:
        stale = s.get(Car, car_id)  # loaded before the AI wrote: a pass's view of it
        s.expunge(stale)
    with sessions() as s:
        car = s.get(Car, car_id)
        car.extra = {"origin": {"fuel": "ai"}, "ai_warnings": ["deposit"]}
        s.commit()
    make_service(sessions, tmp_path, Sites()).update_vehicles()
    with sessions() as s:
        car = s.get(Car, car_id)
        assert car.check_warnings == ["vin_make"] and car.extra["ai_warnings"] == ["deposit"]


def test_a_car_whose_vin_is_gone_loses_its_check_warnings(sessions, tmp_path):
    car_id = add_car(sessions, "1", make="volkswagen", year=2018, vin=OCTAVIA_VIN)
    service = make_service(sessions, tmp_path, Sites())
    service.update_vehicles()
    with sessions() as s:
        s.get(Car, car_id).vin = "0"  # some sites write "0" for none
        s.commit()
    assert service.update_vehicles() == 1
    with sessions() as s:
        assert s.get(Car, car_id).check_warnings == []


def test_a_typo_fixed_in_the_same_ad_is_no_rollback():
    seen = vehicle.record_km([], 182_000, "2026-01-01", car=1)
    seen = vehicle.record_km(seen, 128_000, "2026-01-02", car=1)  # the seller's fix
    assert [r["km"] for r in seen] == [128_000] and not vehicle.rolled_back(seen)
    assert vehicle.rolled_back(vehicle.record_km(seen, 90_000, "2026-06-01", car=2))


def test_an_odd_register_record_breaks_nothing():
    odd = register.facts(RECORD | {
        "MotorMaxVykon": 110.0, "PocetVlastniku": "3", "OrvZadrzeno": "NE",
        "DatumPrvniRegistrace": "2016-02-30T00:00:00", "PravidelnaTechnickaProhlidkaDo": 5,
    })  # fmt: skip
    assert odd["power_kw"] == 110 and odd["owners"] == 3 and not odd["seized"]
    assert odd["first_registration"] is None and odd["stk_until"] is None
    car = Car(title="Octavia", description="", make="skoda", year=2016, first_owner=True)
    assert register.mismatches(odd, car) == ["reg_owners"]
    assert register.facts(RECORD | {"RzZadrzena": "ANO"})["seized"]


def test_claims_of_a_car_bought_in_czechia():
    from adacar.normalize.text import fold

    for said in ("Zakoupeno v ČR", "Původ: Česká republika", "koupeno CZ", "Vůz je z ČR",
                 "Nehavarované, původ ČR"):  # fmt: skip
        assert register.said_bought_here(None, fold(said)), said
    for not_said in ("Nejedná se o auto koupené v ČR", "Dovoz z Německa", "koupeno v Crailsheimu"):
        assert not register.said_bought_here(None, fold(not_said)), not_said
    assert register.said_bought_here("Česká republika", "")


def test_seat_and_cupra_are_one_make_and_hybrids_keep_their_system_power():
    cupra = Car(title="Cupra Leon", description="", make="cupra", year=2016, power_kw=221)
    seat = register.facts(RECORD | {"TovarniZnacka": "SEAT", "MotorMaxVykon": "221 / 5000"})
    assert register.mismatches(seat, cupra) == []
    hybrid = Car(title="Octavia iV", description="", make="skoda", year=2016, power_kw=150,
                 fuel="plugin_hybrid")  # fmt: skip
    assert register.mismatches(register.facts(RECORD), hybrid) == []


def test_a_person_in_business_is_never_looked_up():
    person = company("11111111", "Václav Matoušek", legal_form="101")
    assert resolver.resolve(FakeAres([person]), "Václav Matoušek").method == "person"
    firm = company("22222222", "Václav Matoušek s.r.o.", nace=["45110"])
    res = resolver.resolve(FakeAres([person, firm]), "Václav Matoušek")
    assert [c.company.ico for c in res.candidates] == ["22222222"]  # the person is not shown


def test_a_person_typed_in_by_ico_is_refused(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    sites.ares["33333333"] = ares_record("33333333", "Jan Novák", pravniForma="101")
    service = make_service(sessions, tmp_path, sites, profile={car_id})
    service.run_pass()
    with sessions() as s:
        did = s.get(Car, car_id).dealer_id
    assert service.correct_dealer(did, "33333333") == "person"
    with sessions() as s:
        assert s.get(Dealer, did).ico == "12345678"


def test_another_word_than_a_town_makes_another_company():
    ares = FakeAres([company("1", "FORTEX s.r.o.", nace=["45110"])])
    assert resolver.resolve(ares, "FORTEX - AGS").method == "uncertain"
    baxa = FakeAres([company("2", "BAXA spol. s r.o.", nace=["41200"])])
    assert resolver.resolve(baxa, "Baxa").ico is None  # one word, not the car trade
    assert resolver.resolve(baxa, "Baxa", None).method == "uncertain"


def test_one_name_ares_refuses_does_not_hold_up_the_others(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    first = add_car(sessions, "9", seller_kind="dealer", seller_name="Bad Name")
    for i in range(3):  # Bad Name has the most cars: first in line
        add_car(sessions, f"b{i}", seller_kind="dealer", seller_name="Bad Name")
    handler = sites.handler

    def refuse(request):
        if b"Bad" in request.content:
            return httpx.Response(400, text="CHYBA_VSTUPU")
        return handler(request)

    sites.handler = refuse
    make_service(sessions, tmp_path, sites, profile={car_id, first}).run_pass()
    with sessions() as s:
        assert s.get(Car, car_id).dealer.ico == "12345678"
        assert s.get(Car, first).dealer.match == "error"


def test_warnings_reach_the_cars_even_when_a_later_step_fails(sessions, tmp_path, monkeypatch):
    add_car(sessions, "1", make="volkswagen", year=2018, vin=OCTAVIA_VIN)
    service = make_service(sessions, tmp_path, Sites())
    calls = []
    service.on_changed = lambda: calls.append(True)

    def broken():
        raise ValueError("a broken VAT list")

    monkeypatch.setattr(service.dealers, "apply_vat_list", broken)
    assert service.work() < 3600  # tried again soon
    assert calls == [True]


def test_a_car_no_longer_from_a_dealer_loses_its_card(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    service = make_service(sessions, tmp_path, sites)
    service.dealers.link()
    with sessions() as s:
        s.get(Car, car_id).seller_kind = "private"
        s.commit()
    service.dealers.link()
    with sessions() as s:
        assert s.get(Car, car_id).dealer_id is None


def test_a_dealer_resolved_anew_forgets_the_old_company(sessions, tmp_path):
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    sites.ares["12345678"]["seznamRegistraci"] = {"stavZdrojeIr": "AKTIVNI"}
    sites.isir["12345678"] = "KONKURS"
    service = make_service(sessions, tmp_path, sites, profile={car_id})
    service.run_pass()
    sites.ares["12345679"] = ares_record("12345679", "Auto Plus s.r.o.")  # a namesake now
    with sessions() as s:
        did = s.get(Car, car_id).dealer_id
    service.check_dealer(did, force=True)
    with sessions() as s:
        d = s.get(Dealer, did)
        assert d.match == "uncertain" and d.insolvency == [] and d.warnings == []


def test_the_background_spends_little_of_the_google_quota(sessions, tmp_path, monkeypatch):
    from adacar.checks import dealers

    monkeypatch.setattr(dealers, "GOOGLE_PER_DAY", 1)
    sites = Sites()
    car_id, _ = dealer_setup(sessions, sites)
    other = add_car(sessions, "2", seller_kind="dealer", seller_name="Auto Minus s.r.o.")
    sites.ares["87654321"] = ares_record("87654321", "Auto Minus s.r.o.")
    service = make_service(sessions, tmp_path, sites, {GOOGLE_KEY: "g" * 20},
                           profile={car_id, other})  # fmt: skip
    service.run_pass()
    assert service.dealers.google_quota().details == 1
    with sessions() as s:
        did = s.get(Car, other).dealer_id
    service.check_dealer(did)  # an opened card still may
    assert service.dealers.google_quota().details == 2


def test_an_opened_card_does_not_ask_a_failing_register_on_every_look(sessions, tmp_path):
    sites = Sites()
    sites.register_status = 503
    car_id = add_car(sessions, "1", make="skoda", vin=OCTAVIA_VIN)
    service = make_service(sessions, tmp_path, sites, {REGISTER_KEY: "k" * 20})
    assert service.check_car(car_id) is False
    assert service.check_car(car_id) is True  # not asked again within the hour
    assert len([c for c in sites.calls if c.startswith(register.API)]) == 1
    assert service.check_car(car_id, force=True) is False  # "Ověřit znovu" asks
