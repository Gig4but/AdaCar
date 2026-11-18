import base64
import errno
import hashlib
import json
import subprocess
import sys
import threading
import time
from io import BytesIO

import httpx
import pytest
from conftest import make_ai, make_hw
from PIL import Image
from sqlalchemy import select

from adacar.ai import advisor, vision
from adacar.ai.advisor import Speed
from adacar.ai.ask import schema as ask_schema
from adacar.ai.ask import to_filters
from adacar.ai.catalog import MODELS, TIERS
from adacar.ai.client import LlmClient, LlmError, Reply, ServerDown, Timing, list_models
from adacar.ai.download import DownloadError, RemoteFile, download
from adacar.ai.extract import FIELDS, schema, user_message, validate
from adacar.ai.grammar import compact_grammar
from adacar.ai.runtime import Launch
from adacar.config import Settings
from adacar.core import ParsedCar
from adacar.db.models import Car, CarImage, LlmCache
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.scraper.store import CarStore
from adacar.system.resources import Priority, ResourceManager

GIB = 1024**3
ZERO = Timing(0, 0.0, 0, 0.0, 0.0)


# --- model choice (PLAN §6.6) ------------------------------------------------------------


def icons(advice):
    return [f.icon for f in advice.fits]


def test_advisor_matches_the_expected_hardware_table():
    # Integrated graphics: Simple ~4.5 s per ad, over the red limit: Auto uses rules only.
    minimum = advisor.advise(make_hw(8, "AMD Radeon(TM) Graphics", 0.5, cores=6))
    assert icons(minimum) == ["red", "red", "red"]
    assert minimum.pick is None and minimum.reason == "ai.reason.none"
    fast = {TIERS["good"]: Speed(prompt_tps=3000, output_tps=200, measured=True)}  # ~0.8 s
    measured = advisor.advise(make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8), fast)
    assert icons(measured) == ["red", "green", "red"]
    assert measured.pick.tier == "good" and measured.reason == "ai.reason.igpu"

    gaming = advisor.advise(make_hw(32, "NVIDIA GeForce RTX 4060", 8, cores=8))
    assert icons(gaming) == ["green", "yellow", "yellow"]  # ~0.8 / 1.6 / 2.6 s per ad
    assert gaming.pick.tier == "simple" and gaming.reason == "ai.reason.dgpu"


def test_advisor_too_little_memory_or_disk():
    tiny = advisor.advise(make_hw(4))
    assert tiny.pick is None and tiny.reason == "ai.reason.none"

    full_disk = advisor.advise(make_hw(16, "Intel(R) Graphics", 0.1, disk_gb=3))
    assert all("ai.why.disk" in f.reasons for f in full_disk.fits[1:])
    downloaded = {MODELS[TIERS["good"]].id}
    assert (
        "ai.why.disk"
        not in advisor.advise(
            make_hw(16, "Intel(R) Graphics", 0.1, disk_gb=3), downloaded=downloaded
        )
        .fits[1]
        .reasons
    )


def test_measured_speed_replaces_the_estimate():
    hw = make_hw(32, "NVIDIA GeForce RTX 4060", 8, cores=8)
    slow = {TIERS["good"]: Speed(prompt_tps=40, output_tps=3, measured=True)}
    advice = advisor.advise(hw, measured=slow)
    good = advice.fits[1]
    assert good.speed.measured and good.icon == "red" and "ai.why.slow" in good.reasons
    assert advice.pick.tier == "simple"


def test_launch_settings_follow_the_hardware(tmp_path):
    spec = MODELS[TIERS["simple"]]
    igpu = advisor.launch_for(spec, make_hw(8, "AMD Radeon(TM) Graphics", 0.5, cores=6), tmp_path)
    assert igpu.backend == "vulkan" and igpu.gpu_layers is None and igpu.threads == 5
    assert igpu.mmproj is None  # the vision part is loaded only for images
    cpu = advisor.launch_for(spec, make_hw(8, cores=2), tmp_path, vision=True)
    assert cpu.backend == "cpu" and cpu.threads == 2 and cpu.mmproj is not None
    args = cpu.args(8080)
    assert args[args.index("--device") + 1] == "none" and "--mmproj" in args
    assert cpu.ram_need > igpu.ram_need  # the vision projector needs memory too


def test_heavy_jobs_run_one_at_a_time_on_8_gb_and_by_priority():
    rm = ResourceManager(make_hw(8), available_ram=lambda: 6 * GIB, idle_seconds=lambda: 0)
    assert rm.limits.parallel_heavy == 1 and rm.limits.routing_radius_km == 150
    order, holding = [], []

    def take(priority):
        slot = rm.heavy(priority)
        slot.__enter__()
        holding.append(slot)  # kept: the slot stays taken
        order.append(priority)

    with rm.heavy(Priority.IMPORT):
        threads = [
            threading.Thread(target=take, args=(p,), daemon=True)
            for p in (Priority.IMPORT, Priority.AI, Priority.USER)
        ]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 2
        while len(rm._waiting) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)  # all three are waiting before the slot frees up
    threads[2].join(2)  # USER gets the slot first even though it asked last
    assert order == [Priority.USER]
    holding[0].__exit__(None, None, None)
    threads[1].join(2)
    assert order == [Priority.USER, Priority.AI]
    holding[1].__exit__(None, None, None)
    threads[0].join(2)
    with pytest.raises(TimeoutError), rm.heavy(Priority.AI, timeout=0.05):
        pass
    assert ResourceManager(make_hw(32)).limits.parallel_heavy == 3


def test_busy_means_low_memory_while_the_user_works():
    free = {"ram": 1 * GIB}
    idle = {"s": 5.0}
    rm = ResourceManager(make_hw(8), lambda: free["ram"], lambda: idle["s"])
    assert rm.busy()
    idle["s"] = 600  # the user went away: background work may use the PC
    assert not rm.busy()
    idle["s"], free["ram"] = 5.0, 5 * GIB
    assert not rm.busy() and rm.busy(need=4 * GIB)


def served(data: bytes, honour_range: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        start = 0
        if honour_range and "range" in request.headers:
            start = int(request.headers["range"].split("=")[1].rstrip("-"))
            return httpx.Response(206, content=data[start:])
        return httpx.Response(200, content=data)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_resumes_and_checks_the_hash(tmp_path):
    data = bytes(range(256)) * 4000
    remote = RemoteFile(
        "https://x/model.gguf", "model.gguf", len(data), hashlib.sha256(data).hexdigest()
    )
    (tmp_path / "model.gguf.part").write_bytes(data[:300_000])  # an interrupted download
    seen = []
    path = download(remote, tmp_path, served(data), progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == data and not (tmp_path / "model.gguf.part").exists()
    assert seen[0] > 300_000  # continued, not restarted

    (tmp_path / "other.gguf.part").write_bytes(b"x" * 10)
    other = RemoteFile("https://x/other.gguf", "other.gguf", len(data), "0" * 64)
    with pytest.raises(DownloadError):
        download(other, tmp_path, served(data, honour_range=False))
    assert not (tmp_path / "other.gguf").exists()


def fake_disk(monkeypatch, free: dict):
    from collections import namedtuple

    from adacar.ai import download as dl

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda path: usage(0, 0, free["bytes"]))


def test_a_full_disk_stops_the_download_and_keeps_the_part_file(tmp_path, monkeypatch):
    from adacar.ai.download import SPACE_MARGIN, NoSpace

    data = bytes(range(256)) * 4000
    remote = RemoteFile(
        "https://x/model.gguf", "model.gguf", len(data), hashlib.sha256(data).hexdigest()
    )
    part = tmp_path / "model.gguf.part"
    part.write_bytes(data[:300_000])
    free = {"bytes": SPACE_MARGIN + 100_000}  # less than the rest of the file
    fake_disk(monkeypatch, free)
    requests = []
    refuse = httpx.Client(transport=httpx.MockTransport(requests.append))
    with pytest.raises(NoSpace, match="disk space"):
        download(remote, tmp_path, refuse)
    assert not requests and part.stat().st_size == 300_000  # nothing fetched, nothing lost

    # The disk fills up while writing: the part stays for a later resume.
    free["bytes"] = 10 * GIB
    real_open = type(part).open

    class FullDisk:
        def __init__(self, f):
            self.f = f

        def write(self, chunk):
            raise OSError(errno.ENOSPC, "disk full")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

    def open_(self, mode="r", *args, **kwargs):
        f = real_open(self, mode, *args, **kwargs)
        return FullDisk(f) if "a" in mode else f

    monkeypatch.setattr(type(part), "open", open_)
    with pytest.raises(NoSpace):
        download(remote, tmp_path, served(data))
    monkeypatch.undo()
    assert part.stat().st_size == 300_000
    assert download(remote, tmp_path, served(data)).read_bytes() == data


def test_the_manager_does_not_start_a_download_that_cannot_fit(tmp_path, monkeypatch):
    from adacar.ai.manager import ModelManager
    from adacar.ai.runtime import LlamaServer

    fake_disk(monkeypatch, {"bytes": GIB // 2})
    settings = Settings.load(tmp_path / "config.json")

    def no_http():
        raise AssertionError("nothing may be downloaded")

    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(16, "NVIDIA GeForce RTX 4060", 8, cores=8), http_factory=no_http,
    )  # fmt: skip
    assert manager.start_download()
    manager.wait(5)
    assert manager.download.state == "failed" and "disk space" in manager.download.error


def test_compact_grammar_has_no_whitespace_rules():
    grammar = compact_grammar(schema(("gearbox", "year")))
    root = grammar.splitlines()[0]
    assert root == 'root ::= "{" "\\"rok\\"" ":" v0 "," "\\"prevodovka\\"" ":" v1 "}"'
    assert 'v1 ::= "\\"manuální\\"" | "\\"automatická\\"" | "null"' in grammar
    assert "space" not in grammar


def test_grammar_integers_have_no_leading_zeros():
    import re

    grammar = compact_grammar(schema(("mileage_km",)))
    rule = next(line for line in grammar.splitlines() if line.startswith("v0 ::= "))
    body = rule.removeprefix("v0 ::= ").removesuffix(' | "null"')
    # This rule's GBNF is a regex once the quotes and spaces are gone.
    pattern = re.compile(body.replace('"', "").replace(" ", ""))
    assert all(pattern.fullmatch(n) for n in ("0", "-7", "35000", "123456789"))
    assert not any(pattern.fullmatch(n) for n in ("007", "-01", "1234567890"))


def test_grammar_array_with_a_minimum_length():
    spec = {"type": "array", "items": {"enum": ["a", "b"]}, "minItems": 1, "maxItems": 3}
    grammar = compact_grammar({"properties": {"x": spec}})
    assert 'v0 ::= "[" v0-item ( "," v0-item ){0,2} "]"' in grammar


def fake_server(answer: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                "timings": {
                    "prompt_n": 800,
                    "prompt_ms": 2000,
                    "predicted_n": 60,
                    "predicted_ms": 4000,
                },
            },
        )

    return httpx.MockTransport(handler)


def test_client_sends_a_grammar_and_reports_speed():
    seen = []
    client = LlmClient("http://ai", transport=fake_server({"a": 1}, seen))
    reply = client.chat([{"role": "user", "content": "x"}], schema=schema(("year",)))
    assert reply.data == {"a": 1} and "grammar" in seen[0] and "response_format" not in seen[0]
    assert reply.timing.prompt_tps == 400 and reply.timing.output_tps == 15
    ollama = LlmClient("http://ai", native=False, transport=fake_server({"a": 1}, seen))
    ollama.chat([{"role": "user", "content": "x"}], schema=schema(("year",)))
    assert seen[1]["response_format"]["type"] == "json_schema"
    assert seen[1]["reasoning_effort"] == "none" and "reasoning_effort" not in seen[0]


def test_list_models_of_an_own_server():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "qwen35-2:32k"}, {"id": "a:1"}]})

    assert list_models("https://llm.lan/", httpx.MockTransport(handler)) == ["a:1", "qwen35-2:32k"]
    with pytest.raises(LlmError):
        list_models("https://llm.lan", httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ServerDown):
        list_models("https://llm.lan", httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ServerDown):
        list_models("http://[x")  # passes the http(s) check, but is no address


def test_transcribe_sends_a_small_jpeg_and_reads_no_text_as_empty():
    answers, seen = ["PRODÁM OCTAVII\nCena 189 000 Kč \n", "-"], []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        message = {"content": answers.pop(0)}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    photo = BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(photo, format="PNG")
    client = LlmClient("http://ai", transport=httpx.MockTransport(handler))
    assert vision.transcribe(client, photo.getvalue()).data == "PRODÁM OCTAVII\nCena 189 000 Kč"
    assert vision.transcribe(client, photo.getvalue()).data == ""
    url = seen[0]["messages"][1]["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as sent:
        assert max(sent.size) == vision.MAX_IMAGE_SIDE


def fake_download(manager, *model_ids):
    for model_id in model_ids:
        for f in MODELS[model_id].files():
            path = manager.models_dir / model_id / f.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")


def test_auto_upgrades_after_a_ram_upgrade_and_keeps_the_old_model_until_then(
    tmp_path, monkeypatch
):
    from adacar.ai.manager import ModelManager
    from adacar.ai.runtime import LlamaServer

    # The test PCs' estimates are all red under the real limits; the upgrade steps are the same.
    monkeypatch.setattr(advisor, "GREEN_SECONDS", 15.0)
    monkeypatch.setattr(advisor, "YELLOW_SECONDS", 40.0)
    settings = Settings.load(tmp_path / "config.json")
    hw = {"now": make_hw(8, cores=4)}
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: hw["now"],
    )  # fmt: skip
    simple, good = TIERS["simple"], TIERS["good"]
    assert manager.target().id == simple and manager.active_model() is None
    fake_download(manager, simple)
    manager.run_benchmark = lambda spec: Speed(300, 15, True)
    manager._try_switch(MODELS[simple])
    assert manager.active_model().id == simple

    hw["now"] = make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8)  # RAM and graphics
    assert manager.refresh_hardware()
    assert manager.target().id == good and not manager.tier_still_right()
    assert manager.active_model().id == simple  # the old model works until the new is ready
    fake_download(manager, good)
    manager._try_switch(MODELS[good])
    assert manager.active_model().id == good
    assert (tmp_path / "models" / simple).exists()  # kept: only the user deletes models
    assert manager.delete_model(simple) and not (tmp_path / "models" / simple).exists()
    assert manager.take_notice() == ("ai.notice.upgraded", {"model": MODELS[good].name})
    assert manager.take_notice() is None
    settings.ai.choice = "qwen3-14b"  # picked before the model left the catalog
    assert manager.target().id == good
    settings.ai.choice = "auto"

    # Real work turns out too slow on this PC: Auto moves down a tier.
    for _ in range(40):
        manager.record(Timing(800, 20.0, 60, 30.0, 50.0))
    assert manager.target().id == simple and not manager.tier_still_right()
    assert manager.delete_model(good) and manager.active_model() is None  # in use: on request


def test_deleting_all_stops_the_server_of_a_loaded_model_first(tmp_path, monkeypatch):
    from adacar.ai.manager import ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: make_hw(8, cores=4),
    )  # fmt: skip
    simple = TIERS["simple"]
    fake_download(manager, simple)
    (manager.models_dir / "removed-model").mkdir()
    launch = advisor.launch_for(MODELS[simple], manager.hw, manager.models_dir / simple)
    stopped = []
    monkeypatch.setattr(LlamaServer, "launch", property(lambda self: launch))
    monkeypatch.setattr(manager.server, "stop", lambda: stopped.append(True))
    manager.delete_all()
    assert stopped and not any(manager.models_dir.iterdir())


def test_a_model_is_benchmarked_once_per_pc(tmp_path):
    from adacar.ai.manager import MIN_SAMPLES, ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    hw = {"now": make_hw(16, "NVIDIA GeForce RTX 4060", 8, cores=8)}

    def manager():
        return ModelManager(
            lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
            LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: hw["now"],
            )  # fmt: skip

    runs = []

    def with_benchmark(m):
        def run(spec):
            runs.append(spec.id)
            m._store_speed(spec.id, 3000, 200, samples=MIN_SAMPLES)
            return Speed(3000, 200, True)

        m.run_benchmark = run
        return m

    first = with_benchmark(manager())
    good = first.target().id
    fake_download(first, good)
    first._try_switch(MODELS[good])
    assert runs == [good] and first.active_model().id == good

    first.state.active = None  # e.g. the app closed before the switch was saved
    first._save()
    second = with_benchmark(manager())
    assert second.needs_download()
    second._try_switch(MODELS[good])
    assert runs == [good] and second.active_model().id == good  # the measured speed is reused

    hw["now"] = make_hw(32, "NVIDIA GeForce RTX 4060", 8, cores=8)  # new hardware: measured again
    third = with_benchmark(manager())
    third._try_switch(MODELS[third.target().id])
    assert len(runs) == 2


def test_auto_rejects_a_model_that_benchmarks_too_slow(tmp_path):
    from adacar.ai.manager import ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8),
    )  # fmt: skip
    good = TIERS["good"]
    fake_download(manager, good)
    manager.run_benchmark = lambda spec: Speed(20, 1.5, True)  # far too slow
    manager._try_switch(MODELS[good])
    assert manager.state.active is None and good in manager.state.rejected
    assert manager.target() is None
    settings.ai.choice = "off"
    assert manager.active_model() is None and manager.target() is None


def test_session_waits_when_memory_is_low(tmp_path):
    from adacar.ai.manager import Busy, ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    settings.ai.choice = "simple"  # Auto uses no model on this slow test PC
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log", available_ram=lambda: GIB),
        probe=lambda: make_hw(8, "AMD Radeon(TM) Graphics", 0.5),
    )  # fmt: skip
    with pytest.raises(Busy, match="no_model"), manager.session():
        pass
    fake_download(manager, TIERS["simple"])
    manager.state.active = TIERS["simple"]
    manager.resources.available_ram = lambda: GIB
    manager.resources.idle_seconds = lambda: 0  # the user is working
    with pytest.raises(Busy, match="pc_busy"), manager.session():
        pass
    manager.resources.idle_seconds = lambda: 900  # away, but still not enough RAM to load
    with pytest.raises(Busy, match="memory"), manager.session():
        pass

    def no_slot(priority, timeout=None):
        raise TimeoutError("no free slot")

    manager.resources.heavy = no_slot
    with pytest.raises(Busy, match="pc_busy"), manager.session(priority=Priority.USER):
        pass


def test_real_work_changes_the_tier_only_after_enough_calls(tmp_path):
    from adacar.ai.manager import MIN_SAMPLES, ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    settings.ai.choice = "simple"  # Auto uses no model on this slow test PC
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(8, "AMD Radeon(TM) Graphics", 0.5),
    )  # fmt: skip
    fake_download(manager, TIERS["simple"])
    manager.state.active = TIERS["simple"]
    for _ in range(MIN_SAMPLES - 1):
        manager.record(Timing(800, 20.0, 60, 30.0, 50.0))
    assert manager.measured() == {}  # a few slow calls are not a verdict yet
    manager.record(Timing(800, 20.0, 60, 30.0, 50.0))
    assert TIERS["simple"] in manager.measured()


def test_amd_integrated_graphics_with_a_large_reservation_are_not_a_graphics_card():
    from dataclasses import replace

    from adacar.system.hardware import Gpu

    def gpu(name, vram_gb):
        return Gpu(name, "AMD", int(vram_gb * GIB), 8 * GIB)

    for name in ("AMD Radeon(TM) Graphics", "AMD Radeon 780M Graphics", "AMD Radeon(TM) 890M",
                 "AMD Radeon(TM) 8060S Graphics", "AMD Radeon(TM) Vega 8 Graphics"):  # fmt: skip
        assert not gpu(name, 8).is_dedicated, name
    for name in ("AMD Radeon RX 7600M XT", "AMD Radeon RX 6600", "NVIDIA GeForce RTX 4060"):
        assert gpu(name, 8).is_dedicated, name
    hw = make_hw(32, "AMD Radeon 780M Graphics", 16, cores=8)
    assert hw.dedicated_gpu is None and advisor.speed_class(hw).name.startswith("AMD Radeon 760M")
    both = replace(hw, gpus=(*hw.gpus, gpu("NVIDIA GeForce RTX 4060", 8)))
    assert both.dedicated_gpu.name == "NVIDIA GeForce RTX 4060"


def test_launch_is_hashable_for_restart_checks(tmp_path):
    a = Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, GIB)
    assert a == Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, GIB)
    with pytest.raises(LlmError):
        raise LlmError("x")


def test_hardware_probe_is_quick_and_sane():
    from adacar.system.hardware import probe

    started = time.perf_counter()
    hw = probe()
    assert time.perf_counter() - started < 2
    assert hw.total_ram > GIB and hw.physical_cores >= 1 and hw.free_disk > 0
    assert hw.fingerprint() == probe().fingerprint()  # free memory does not change it


def test_another_model_waits_until_the_running_one_is_released(tmp_path):
    from adacar.ai.runtime import LlamaServer

    class Proc:
        def __init__(self, launch):
            self.launch, self.ended = launch, False

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True
            events.append(f"stop {self.launch.weights.name}")

        def wait(self, timeout=None):
            return 0

    events = []
    server = LlamaServer(tmp_path, tmp_path / "log", available_ram=lambda: 64 * GIB)
    server._start = lambda launch: setattr(server, "_proc", Proc(launch))
    a = Launch(tmp_path / "a.gguf", None, "cpu", 0, 4, 4096, GIB)
    b = Launch(tmp_path / "b.gguf", None, "cpu", 0, 4, 4096, GIB)
    inside = threading.Event()

    def use_a():
        with server.use(a):
            inside.set()
            time.sleep(0.3)
            events.append("a done")

    worker = threading.Thread(target=use_a)
    worker.start()
    inside.wait(5)
    with server.use(b):
        events.append("b running")
    worker.join(5)
    assert events == ["a done", "stop a.gguf", "b running"]


def test_a_custom_server_does_not_change_the_local_model_speed(tmp_path):
    from adacar.ai.manager import ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    settings.ai.choice = "simple"  # Auto uses no model on this slow test PC
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(8, "AMD Radeon(TM) Graphics", 0.5),
    )  # fmt: skip
    fake_download(manager, TIERS["simple"])
    manager.state.active = TIERS["simple"]
    settings.ai.server_url = "http://127.0.0.1:11434"
    manager.record(Timing(800, 1.0, 60, 1.0, 2.0))
    assert manager.state.speeds == {}
    settings.ai.server_url = ""
    manager.record(Timing(800, 1.0, 60, 1.0, 2.0))
    assert TIERS["simple"] in manager.state.speeds


def fake_llama(tmp_path, monkeypatch, runs: list, free_ram: dict):
    """A LlamaServer whose llama-server.exe is faked. Each start takes the next of `runs`:
    "ok", "slow" (never ready) or the text the process logs before it exits."""
    from adacar.ai import runtime

    for backend in ("vulkan", "cpu"):
        exe = tmp_path / "llama.cpp" / backend / "llama-server.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
    started = []

    class Proc:
        pid = 1

        def __init__(self, args, stdout, **kwargs):
            self.args, self.run, self.code = args, runs.pop(0), None
            started.append(self)
            if self.run not in ("ok", "slow"):
                stdout.write(self.run.encode())
                self.code = 1

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0

        kill = terminate

        def wait(self, timeout=None):
            return self.code

    def health(request):
        return httpx.Response(200 if started[-1].run == "ok" else 503)

    real_client = httpx.Client
    monkeypatch.setattr(runtime.subprocess, "Popen", Proc)
    monkeypatch.setattr(
        runtime.httpx, "Client", lambda timeout: real_client(transport=httpx.MockTransport(health))
    )
    monkeypatch.setattr(runtime.winjob, "bind_to_app", lambda pid: True)
    monkeypatch.setattr(runtime, "START_TIMEOUT", 0.5)
    server = runtime.LlamaServer(
        tmp_path, tmp_path / "logs" / "llama-server.log", available_ram=lambda: free_ram["bytes"]
    )
    return server, started


PORT_TAKEN = "error: couldn't bind HTTP server socket"


def test_llama_server_start_ready_exited_or_timed_out(tmp_path, monkeypatch):
    from adacar.ai.runtime import ServerFailed

    runs = ["ok", PORT_TAKEN, "ggml_vulkan: device lost", "slow"]
    server, started = fake_llama(tmp_path, monkeypatch, runs, {"bytes": 64 * GIB})
    launch = Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, GIB)
    server._start(launch)
    assert server.running and str(server._port) in started[0].args
    server.stop()
    with pytest.raises(ServerFailed, match="exited") as port_taken:
        server._start(launch)
    assert port_taken.value.transient  # another program took the port: worth a retry
    with pytest.raises(ServerFailed, match="exited") as crashed:
        server._start(launch)
    assert not crashed.value.transient  # only this start's log lines count
    with pytest.raises(ServerFailed, match="ready") as slow:
        server._start(launch)
    assert slow.value.transient and not server.running and started[-1].code == 0


def test_vulkan_is_given_up_only_when_it_crashes(tmp_path, monkeypatch):
    from adacar.ai.runtime import NotEnoughMemory, ServerFailed

    runs = [PORT_TAKEN, PORT_TAKEN, PORT_TAKEN, "ok", "ggml_vulkan: device lost"]
    free = {"bytes": 4 * GIB}
    server, started = fake_llama(tmp_path, monkeypatch, runs, free)
    # 2 GB in RAM next to a graphics card; 6 GB with the whole model in RAM.
    gpu = Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, 2 * GIB, 6 * GIB)
    with pytest.raises(ServerFailed):
        server.ensure(gpu)
    assert len(started) == 2 and not server.vulkan_broken  # tried twice, Vulkan not blamed
    server.ensure(gpu)
    assert len(started) == 4 and not server.vulkan_broken  # the port race was retried
    server.stop()
    with pytest.raises(NotEnoughMemory):  # the CPU build would need more than is free
        server.ensure(gpu)
    assert server.vulkan_broken and len(started) == 5
    free["bytes"], runs[:] = 64 * GIB, ["ok"]
    server.ensure(gpu)
    assert "cpu" in started[-1].args[0] and "--device" in started[-1].args
    server.stop()

    spec = MODELS[TIERS["good"]]
    launch = advisor.launch_for(spec, make_hw(16, "NVIDIA GeForce RTX 3050", 4), tmp_path)
    assert launch.ram_need < launch.cpu_ram_need == advisor.ram_need(spec)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_llama_server_ends_with_the_app():
    import psutil

    app = (
        "import subprocess, sys\n"
        "from adacar.system import winjob\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, winjob.bind_to_app(child.pid))\n"
    )  # binds a child, then ends like a crashed app: without stopping it
    out = subprocess.run([sys.executable, "-c", app], capture_output=True, text=True, timeout=60)
    pid, bound = out.stdout.split()
    try:
        assert bound == "True"
        deadline = time.monotonic() + 10
        while psutil.pid_exists(int(pid)) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not psutil.pid_exists(int(pid))
    finally:
        if psutil.pid_exists(int(pid)):
            psutil.Process(int(pid)).kill()


# --- the car prompt and its checks (PLAN §6) ----------------------------------------------

AD = (
    "Škoda Octavia Combi 2.0 TDI\n\nProdám, r.v. 2017, najeto 186 tis. km, 110 kW, DSG."
    " Servisní knížka, nehavarováno. VIN: TMBJJ7NE8H0123456\nPři rychlém jednání sleva,"
    " prohlídka v Kolíně po domluvě."
)


def test_validate_keeps_only_what_the_ad_supports():
    data = {
        "znacka": "Škoda", "model": "Octavia", "rok": 2017, "km": 186000, "vykon_kw": 110,
        "palivo": "nafta", "prevodovka": "automatická", "karoserie": "kombi",
        "vin": "TMBJJ7NE8H0123456", "servisni_knizka": True, "prvni_majitel": True,
        "havarovano": False,
    }  # fmt: skip
    r = validate(data, AD)
    assert r.values == {
        "make": "skoda", "model": "Octavia", "year": 2017, "mileage_km": 186000,
        "power_kw": 110, "fuel": "diesel", "gearbox": "automatic", "body": "combi",
        "vin": "TMBJJ7NE8H0123456", "service_book": True, "crashed": False,
    }  # fmt: skip
    assert r.dropped == ["prvni_majitel=True"]  # the ad does not say so
    assert r.is_offer and r.raw == data

    invented = {
        "znacka": "Volkswagen", "model": "Golf", "rok": 2015, "km": 999, "vykon_kw": 2000,
        "vin": "WVWZZZ1KZAW000001", "havarovano": True,
    }  # fmt: skip
    r = validate(invented, AD)
    assert r.values == {} and len(r.dropped) == 7

    r = validate({"nabidka": False, "varovani": ["záloha předem", "nesmysl"]}, AD)
    assert r.is_offer and r.warnings == []  # no buyer's or parts words, no deposit asked
    assert r.dropped == ["nabidka=False", "varovani=záloha předem"]
    scam = AD + " Pošlete zálohu, auto je v Německu, km nelze zaručit."
    every = ["záloha předem", "prodejce v zahraničí", "nezaručené km"]
    assert validate({"varovani": every}, scam).warnings == ["deposit", "abroad", "km_unverified"]
    assert validate({"varovani": every}, AD + " Kupujte vozy s garancí!").warnings == []
    wheels = "Prodám orig. disky a pneu Toyota Corolla"
    assert not validate({"nabidka": False}, wheels).is_offer
    assert validate({"prevodovka": "manuální"}, wheels).dropped == ["prevodovka=manuální"]
    assert validate({"model": "Škoda Octavia"}, AD).values == {"model": "Octavia"}
    suv = validate({"karoserie": "kombi", "palivo": "nafta"}, "Dacia Duster 1.6 SCe, SUV")
    assert suv.values == {} and suv.dropped == ["palivo=nafta", "karoserie=kombi"]  # guesses
    assert validate({"rok": 2014, "vykon_kw": 103}, "r.v. 2014 103 kW").values == {
        "year": 2014, "power_kw": 103
    }  # fmt: skip


def test_schema_asks_only_for_missing_fields():
    assert list(schema(("year", "vin"))["properties"]) == ["rok", "vin"]
    untrusted = schema(("year",), untrusted=True)
    assert list(untrusted["properties"]) == ["nabidka", "rok", "varovani"]
    assert untrusted["required"] == ["nabidka", "rok", "varovani"]
    assert user_message("a\n\n\n\nb") == "Inzerát:\na\n\nb"


def test_ask_answer_becomes_filters():
    asked = to_filters({
        "slova": " tažné ", "znacka": ["Škoda", "Bugatti"], "palivo": ["nafta"],
        "prevodovka": [], "karoserie": ["kombi"], "prodejce": ["Soukromý prodejce"],
        "kraj": ["Středočeský kraj", "Atlantida"], "cena_od": None, "cena_do": 300000,
        "rok_od": 2015, "km_do": 5, "vykon_od": None, "odkud": "Kolín", "minut": 60, "dni": 7,
    })  # fmt: skip
    f = asked.filters
    assert (f.q, f.make, f.fuel, f.body, f.seller_kind, f.kraj) == (
        "tažné", ["skoda"], ["diesel"], ["combi"], ["private"], ["Středočeský kraj"]
    )  # fmt: skip
    assert (f.price_max, f.year_min, f.km_max, f.travel_max, f.posted_days) == (
        300000, 2015, None, 60, 7
    )  # fmt: skip
    assert asked.place == "Kolín"
    assert to_filters({"minut": 60}).filters.travel_max is None  # no place to start from
    assert "Středočeský kraj" in str(ask_schema())


def test_an_answer_of_the_wrong_shape_is_a_bad_answer():
    from adacar.ai.ask import ask
    from adacar.ai.client import BadAnswer

    client = LlmClient("http://ai", transport=fake_server({"znacka": 5}, []))
    with pytest.raises(BadAnswer):
        ask(client, "něco")


def test_photos_are_classified_from_a_small_copy():
    seen = []
    photo = BytesIO()
    Image.new("RGB", (1600, 1200), "white").save(photo, format="JPEG")
    client = LlmClient("http://ai", transport=fake_server({"foto": "předek"}, seen))
    assert vision.classify(client, photo.getvalue()).data == "front"
    url = seen[0]["messages"][1]["content"][0]["image_url"]["url"]
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as sent:
        assert max(sent.size) == vision.PHOTO_SIDE
    other = LlmClient("http://ai", transport=fake_server({"foto": "jiné"}, seen))
    assert vision.classify(other, photo.getvalue()).data == "other"


def test_text_rules_find_warning_signs_and_a_price():
    from adacar.normalize.cars import price_in_text, warnings_in_text

    text = "Jsem v zahraničí, auto pošlu. Pošlete zálohu předem na účet. Stav km nelze zaručit."
    assert warnings_in_text("Octavia", text) == ["deposit", "abroad", "km_unverified"]
    assert warnings_in_text("Octavia", "Záloha při převzetí v Kolíně.") == []
    assert price_in_text("Cena 289 000 Kč, dohoda") == 289000
    assert price_in_text("za 45.000,- ") == 45000
    assert price_in_text("1 Kč") is None and price_in_text("Octavia 2017") is None


# --- the enricher (ai/enrich.py) -----------------------------------------------------------


@pytest.fixture
def ai_setup(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    settings = Settings.load(tmp_path / "config.json")
    return sessions, settings, make_ai(tmp_path, settings, sessions)


def add_car(sessions, external_id, source="bazos", **fields) -> int:
    fields.setdefault("title", f"Auto {external_id}")
    with sessions() as s:
        store = CarStore(s, source)
        store.add(ParsedCar(external_id=external_id, **fields))
        store.commit()
        return s.scalars(select(Car.id).order_by(Car.id.desc())).first()


class FakeSession:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0
        self.model = "fake"

    def chat(self, messages, schema=None, max_tokens=0, temperature=0):
        from adacar.ai.client import Reply

        self.calls += 1
        data = {k: v for k, v in self.answer.items() if k in schema["properties"]}
        return Reply(data, Timing(800, 2.0, 60, 4.0, 6.0))


def fake_ai_session(ai, monkeypatch, client):
    from contextlib import contextmanager

    @contextmanager
    def session(vision=False, priority=Priority.AI):
        yield client

    monkeypatch.setattr(ai.manager, "session", session)
    monkeypatch.setattr(type(ai.manager), "ready", property(lambda self: True))


def test_rules_mark_warning_signs_and_a_price_far_below_similar_cars(ai_setup):
    sessions, _, ai = ai_setup
    octavia = {"make": "skoda", "model": "Octavia Combi", "year": 2017}
    for i in range(6):
        add_car(sessions, f"o{i}", price=300_000 + i * 5000, mileage_km=150_000, **octavia)
    cheap = add_car(sessions, "cheap", price=120_000, mileage_km=140_000, **octavia)
    worn = add_car(sessions, "worn", price=120_000, mileage_km=400_000, **octavia)
    crashed = add_car(sessions, "crashed", price=120_000, crashed=True, **octavia)
    deposit = add_car(sessions, "deposit", description="Pošlete zálohu předem.", price=310_000,
                      **octavia)  # fmt: skip
    assert ai.enricher.run_rules() == 2
    with sessions() as s:
        assert s.get(Car, cheap).warnings == ["price_low"]
        assert s.get(Car, worn).warnings == [] and s.get(Car, crashed).warnings == []
        assert s.get(Car, deposit).warnings == ["deposit"]
        s.get(Car, deposit).extra = {"ai_warnings": ["abroad"]}  # the AI's are kept
        s.commit()
    assert ai.enricher.run_rules() == 2
    with sessions() as s:
        assert s.get(Car, deposit).warnings == ["deposit", "abroad"]


def test_ai_fills_only_missing_fields_and_caches(ai_setup, monkeypatch):
    sessions, _, ai = ai_setup
    text = AD + " Záloha předem nutná."
    car_id = add_car(sessions, "1", title="Octavia combi", description=text, fuel="petrol",
                     seller_kind="private")  # fmt: skip
    every = {
        "make": "skoda", "model": "Octavia", "year": 2017, "mileage_km": 186000,
        "fuel": "diesel", "gearbox": "automatic", "power_kw": 110, "body": "combi",
        "vin": "TMBJJ7NE8H0123456", "service_book": True, "first_owner": False, "crashed": False,
    }  # fmt: skip
    assert set(every) == set(FIELDS)
    dealer = add_car(sessions, "2", source="tipcars", description=AD, seller_kind="dealer",
                     **every)  # fmt: skip
    add_car(sessions, "3", description="krátký text")  # nothing to read
    fake = FakeSession({
        "nabidka": True, "znacka": "Škoda", "model": "Octavia", "rok": 2017, "km": 186000,
        "palivo": "nafta", "prevodovka": "automatická", "vykon_kw": 110,
        "servisni_knizka": True, "havarovano": False, "varovani": ["záloha předem"],
    })  # fmt: skip
    fake_ai_session(ai, monkeypatch, fake)
    with sessions() as s:
        assert ai.enricher.queue(s) == [car_id]  # the dealer's car has every field
    assert ai.enricher.run_ai() == 1
    with sessions() as s:
        car = s.get(Car, car_id)
        assert car.fuel == "petrol"  # the source said so: kept
        assert (car.make, car.year, car.mileage_km, car.gearbox) == (
            "skoda",
            2017,
            186000,
            "automatic",
        )
        assert car.service_book is True and car.crashed is False
        assert car.extra["origin"]["year"] == "ai" and "fuel" not in car.extra["origin"]
        assert car.warnings == ["deposit"] and car.extra["ai_warnings"] == ["deposit"]
        assert car.ai_hash == car.content_hash
        assert s.scalar(select(LlmCache.key)) is not None
        assert s.get(Car, dealer).ai_hash is None
        assert ai.enricher.queue(s) == []  # done until the ad changes

    # The same ad again (listed anew under another id): answered from the cache.
    again = add_car(sessions, "4", title="Octavia combi", description=text, fuel="petrol",
                    seller_kind="private")  # fmt: skip
    assert ai.enricher.run_ai() == 1
    assert fake.calls == 1
    with sessions() as s:
        assert s.get(Car, again).year == 2017


def test_a_failing_ad_is_skipped_and_a_dead_server_stops_the_run(ai_setup, monkeypatch):
    from adacar.ai.client import LlmError, ServerDown

    sessions, _, ai = ai_setup
    text = " Prodám auto v dobrém stavu." * 10
    broken = add_car(sessions, "1", description="ERROR" + text)
    odd = add_car(sessions, "2", description="ODD" + text)
    fine = add_car(sessions, "3", description="FINE r.v. 2015" + text)
    fake = FakeSession({"rok": 2015})
    answer = fake.chat

    def chat(messages, **kw):
        if "ERROR" in messages[1]["content"]:
            raise LlmError("AI server answered 500: context too long")
        if "ODD" in messages[1]["content"]:
            raise KeyError("something unexpected")
        return answer(messages, **kw)

    fake.chat = chat
    fake_ai_session(ai, monkeypatch, fake)
    assert ai.enricher.run_ai() == 3
    with sessions() as s:
        for car_id in (broken, odd):  # not first in the queue again at the next run
            assert s.get(Car, car_id).ai_hash == s.get(Car, car_id).content_hash
        assert s.get(Car, fine).year == 2015

    down = add_car(sessions, "4", description="DOWN" + text)

    def unreachable(messages, **kw):
        raise ServerDown("AI server unreachable")

    fake.chat = unreachable
    with pytest.raises(ServerDown):
        ai.enricher.run_ai()
    with sessions() as s:
        assert s.get(Car, down).ai_hash is None  # tried again when the server is back


def test_quitting_mid_answer_is_not_an_error(ai_setup, monkeypatch, caplog):
    from adacar.ai.client import ServerDown

    sessions, _, ai = ai_setup
    add_car(sessions, "1", description="Prodám auto v dobrém stavu." * 10)
    fake = FakeSession({})

    def quitting(messages, **kw):  # the app stops the AI server while it answers
        ai.enricher.stop()
        raise ServerDown("AI server unreachable")

    fake.chat = quitting
    fake_ai_session(ai, monkeypatch, fake)
    ai.enricher._run()
    assert ai.enricher.progress.phase == "done" and not ai.enricher.progress.running
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_profile_matches_come_first(ai_setup):
    sessions, _, ai = ai_setup
    text = "Prodám auto v dobrém stavu." * 10
    first, second, third = (add_car(sessions, str(i), description=text) for i in range(3))
    with sessions() as s:
        assert ai.enricher.queue(s) == [third, second, first]  # newest first
    ai.enricher.profile_cars = lambda s: {first}
    with sessions() as s:
        assert ai.enricher.queue(s) == [first, third, second]


def photo_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGB", (800, 600), "white").save(out, format="JPEG")
    return out.getvalue()


def test_the_cover_is_the_first_photo_of_the_car_from_the_front(ai_setup, monkeypatch):
    sessions, _, ai = ai_setup
    urls = [f"https://img.example/{n}.jpg" for n in ("logo", "front", "side")]
    car_id = add_car(sessions, "1", images=urls)
    broken = add_car(
        sessions, "2", images=[f"https://img.example/{n}.jpg" for n in ("gone", "html")]
    )
    kinds = {"logo": "jiné", "front": "předek", "side": "bok nebo zadek"}
    asked = []

    class Classifier(FakeSession):
        def chat(self, messages, schema=None, max_tokens=0, temperature=0):
            from adacar.ai.client import Reply

            asked.append(1)
            return Reply({"foto": kinds[self.next]}, Timing(300, 1.0, 5, 1.0, 2.0))

    client = Classifier({})
    fake_ai_session(ai, monkeypatch, client)

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.strip("/").removesuffix(".jpg")
        if name == "gone":
            return httpx.Response(404)
        if name == "html":
            return httpx.Response(200, content=b"<html>not found</html>")
        client.next = name
        return httpx.Response(200, content=photo_bytes())

    ai.enricher.http_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    with sessions() as s:
        assert ai.enricher.cover_queue(s) == [broken, car_id]
    assert ai.enricher.run_covers() == 2
    assert len(asked) == 2  # the side view was not needed
    with sessions() as s:
        car = s.get(Car, car_id)
        assert [i.kind for i in car.images] == ["other", "front", None]
        assert [i.url for i in car.photos] == [urls[1], urls[2], urls[0]]
        assert [i.kind for i in s.get(Car, broken).images] == ["failed", "failed"]
        assert ai.enricher.cover_queue(s) == []
    ai.enricher.profile_cars = lambda s: set()  # only the profile's cars are looked at
    add_car(sessions, "3", images=["https://img.example/front.jpg"])
    with sessions() as s:
        assert ai.enricher.cover_queue(s) == []
    ai.enricher.profile_cars = lambda s: None
    for model_id, looks in (("qwen3.5-2b", False), ("qwen3-vl-2b", True)):
        monkeypatch.setattr(ai.manager, "active_model", lambda m=model_id: MODELS[m])
        asked.clear()
        assert (ai.enricher.run_covers() == 1) == looks  # a Qwen3.5 model can not tell
        assert len(asked) == looks


def test_an_added_picture_is_read_into_the_ad(ai_setup, monkeypatch, tmp_path):
    from adacar.ai.enrich import IMAGE_TEXT_HEADER

    sessions, _, ai = ai_setup
    car_id = add_car(sessions, "1", source="manual", title="Inzerát z obrázku",
                     extra={"title_from_picture": True})  # fmt: skip
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "a.jpg").write_bytes(photo_bytes())
    with sessions() as s:
        car = s.get(Car, car_id)
        car.images.append(CarImage(url="/uploads/a.jpg", kind="upload", created_at=car.updated_at))
        car.ai_hash = car.content_hash
        s.commit()
    monkeypatch.setattr(
        vision, "transcribe", lambda client, data: Reply("Prodám Octavii\nr.v. 2017", ZERO)
    )
    fake_ai_session(ai, monkeypatch, FakeSession({}))
    assert ai.enricher.run_pictures() == 1
    with sessions() as s:
        car = s.get(Car, car_id)
        assert car.title == "Prodám Octavii" and car.ai_hash is None  # read by the AI next
        assert car.description == IMAGE_TEXT_HEADER + "\nProdám Octavii\nr.v. 2017"
        assert car.images[0].text == "Prodám Octavii\nr.v. 2017"
    assert ai.enricher.run_pictures() == 0  # read once


def test_scrape_runner_starts_enrichment_after_a_run(tmp_path):
    from adacar.services import build

    started = []
    settings = Settings.load(tmp_path / "config.json")

    def factory(st, sessions, data_dir, *_):
        ai = make_ai(tmp_path, st, sessions)
        ai.enricher.start = lambda: started.append(True)
        return ai

    svc = build(
        settings, db_path=tmp_path / "t.db", data_dir=tmp_path, ai_factory=factory,
        resources=ResourceManager(make_hw(16)), refreshes=(), routing_updates=False,
    )  # fmt: skip
    svc.runner.hooks.after_run()
    assert started == [True]


# --- what the M3 review found ------------------------------------------------------------


def test_negated_and_misplaced_words_are_no_evidence():
    from adacar.normalize.cars import warnings_in_text

    assert warnings_in_text("Nepožaduji zálohu předem, jen osobní předání.") == []
    assert warnings_in_text("Záloha není nutná, bez zálohy předem.") == []
    assert warnings_in_text("Najeto 100000 km garantuji, tachometr ověřen.") == []
    assert warnings_in_text("Technicky v zahraničním servisu, zavolejte předem.") == []
    assert validate({"servisni_knizka": True}, "Bez servisní knížky.").values == {}
    assert validate({"servisni_knizka": True}, "Servisní knížka chybí.").values == {}
    assert validate({"havarovano": True}, "Bez poškození, top stav.").values == {}
    auto = validate({"prevodovka": "automatická"}, "Octavia, bez automatu, 6 stupňů.")
    assert auto.values == {}
    assert validate({"prevodovka": "manuální"}, "Octavia, 6 st. manuál").values == {
        "gearbox": "manual"
    }
    assert validate({"nabidka": False}, "Prodám Octavii, nové pneu, zimní kola navíc").is_offer
    assert validate({"km": 103}, "103 kW, najeto 185000 km").values == {}
    assert validate({"km": 120000}, "Octavia 120tkm").values == {"mileage_km": 120000}
    assert validate({"rok": 2015}, "STK do 2015").values == {}


def test_a_pasted_price_is_the_price_not_an_instalment():
    from adacar.normalize.cars import price_in_text

    assert price_in_text("Splátka 5 000 Kč měsíčně, cena 289000 Kč") == 289000
    assert price_in_text("Služby: pojištění 12 000 Kč ročně\nCena 250 000 Kč") == 250000
    assert price_in_text("Cena: dohodou, záloha 5 000 Kč, 150 000 Kč") == 150000


def test_no_price_warning_for_other_currencies_or_no_price(ai_setup):
    sessions, _, ai = ai_setup
    golf = {"make": "volkswagen", "model": "Golf", "year": 2016, "mileage_km": 120_000}
    for i in range(6):
        add_car(sessions, f"g{i}", price=250_000, **golf)
    euro = add_car(sessions, "eur", price=9000, price_currency="EUR", **golf)
    zero = add_car(sessions, "zero", price=1, **golf)
    ai.enricher.run_rules()
    with sessions() as s:
        assert s.get(Car, euro).warnings == [] and s.get(Car, zero).warnings == []


def test_a_changed_ad_keeps_what_the_ai_read_until_the_source_says_it(ai_setup):
    sessions, _, ai = ai_setup
    car_id = add_car(sessions, "1", description="Octavia " * 30, price=200_000)
    with sessions() as s:
        car = s.get(Car, car_id)
        car.year, car.fuel = 2017, "diesel"
        car.extra = {"origin": {"year": "ai", "fuel": "ai"}, "ai_warnings": ["deposit"]}
        s.commit()
    add_car(sessions, "1", description="Octavia " * 30, price=190_000, fuel="petrol")
    with sessions() as s:
        car = s.get(Car, car_id)
        assert car.price == 190_000 and car.year == 2017  # still unknown to the site
        assert car.fuel == "petrol" and car.extra["origin"] == {"year": "ai"}
        assert car.extra["ai_warnings"] == ["deposit"]


def test_a_question_names_its_place_and_prices_go_in_order():
    from adacar.ai.ask import ask

    answer = {"odkud": "Praha", "minut": 30, "cena_od": 300000, "cena_do": 100000}
    client = LlmClient("http://ai", transport=fake_server(answer, []))
    asked = ask(client, "levné kombi")  # no place in the question: an invented one
    assert asked.place == "" and asked.filters.travel_max is None
    assert (asked.filters.price_min, asked.filters.price_max) == (100000, 300000)
    assert ask(client, "kombi do půl hodiny od Prahy").place == "Praha"


def test_a_question_shares_the_model_loaded_for_pictures(tmp_path, monkeypatch):
    from adacar.ai.manager import ModelManager
    from adacar.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    settings.ai.choice = "excellent"
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: make_hw(16, cores=8),
    )  # fmt: skip
    model = TIERS["excellent"]
    fake_download(manager, model)
    manager.state.active = model
    pictures = advisor.launch_for(MODELS[model], manager.hw, manager.models_dir / model, True)
    used = []
    monkeypatch.setattr(LlamaServer, "launch", property(lambda self: pictures))

    def ensure(launch):
        used.append(launch)
        return "http://ai"

    monkeypatch.setattr(manager.server, "ensure", ensure)
    with manager.session(priority=Priority.USER):
        pass
    assert used == [pictures]  # no wait for the pictures to finish, no reload


def test_a_finished_download_starts_the_enricher(tmp_path):
    from adacar.ai.services import make_ai_services

    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    settings = Settings.load(tmp_path / "config.json")
    ai = make_ai_services(settings, make_sessionmaker(engine), tmp_path)
    assert ai.manager.on_switched == ai.enricher.start
    started = []
    ai.manager.on_switched = lambda: started.append("switched")
    ai.manager.run_benchmark = lambda spec: Speed(3000, 200, True)
    ai.manager._try_switch(MODELS[TIERS["simple"]])
    assert started == ["switched"]


def test_an_added_picture_does_not_keep_the_sites_photos_from_the_cover_check(ai_setup):
    sessions, _, ai = ai_setup
    car_id = add_car(sessions, "1", images=["https://img.example/a.jpg"])
    with sessions() as s:
        car = s.get(Car, car_id)
        car.images.append(CarImage(url="/uploads/a.jpg", kind="upload", position=1,
                                   created_at=car.updated_at))  # fmt: skip
        s.commit()
        assert ai.enricher.cover_queue(s) == [car_id]
