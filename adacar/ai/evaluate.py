"""`adacar eval`: accuracy and speed of the models on the golden set (PLAN §6).

Golden ads (tests/fixtures/eval/car_ads.jsonl, built by tools/make_golden.py) carry the
fields the seller filled in on the site's form. The model reads the title and the text and
is asked for every field, as for a car the source told nothing about.

Reported per model and field: how many the form has, how many the model answered (after the
checks of `extract.validate`), right and wrong; then the ads the model took for no car sale,
the claims the checks dropped, and seconds and tokens per ad.
"""

import argparse
import json
import statistics
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from adacar import paths
from adacar.ai import advisor
from adacar.ai.catalog import MODELS, TIERS
from adacar.ai.client import LlmClient, LlmError
from adacar.ai.extract import FIELDS, Extraction, extract
from adacar.ai.runtime import LlamaServer
from adacar.normalize.text import fold
from adacar.system import hardware

GOLDEN = Path(__file__).parents[2] / "tests" / "fixtures" / "eval" / "car_ads.jsonl"
UNTRUSTED = {"annonce"}  # classifieds: also asked whether it sells a car, and warning signs


def load_golden(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def same(field: str, label, answer) -> bool:
    if field == "model":  # "Octavia" is right for "Octavia Combi"
        return fold(str(answer)).split()[:1] == fold(str(label)).split()[:1]
    return answer == label


def score(golden: list[dict], results: list[Extraction]) -> dict[str, Counter]:
    """Field -> counts of labelled, answered, right and wrong (answered and labelled)."""
    out = {name: Counter() for name in FIELDS}
    for ad, result in zip(golden, results, strict=True):
        for name in FIELDS:
            label, answer = ad["labels"].get(name), result.values.get(name)
            c = out[name]
            c["labelled"] += label is not None
            c["answered"] += answer is not None
            if label is not None and answer is not None:
                c["right" if same(name, label, answer) else "wrong"] += 1
    return out


def run_model(client: LlmClient, golden: list[dict]) -> tuple[list[Extraction], list[float]]:
    results, seconds = [], []
    for ad in golden:
        started = time.perf_counter()
        try:
            result = extract(client, ad["text"], FIELDS, ad["id"].split(":")[0] in UNTRUSTED)
        except LlmError as exc:
            print(f"  {ad['id']}: {exc}")
            result = Extraction()
        seconds.append(time.perf_counter() - started)
        results.append(result)
    return results, seconds


def report(name: str, golden: list[dict], results: list[Extraction], seconds: list[float]) -> None:
    print(f"\n== {name}: {len(golden)} ads")
    print(f"{'field':14}{'labelled':>9}{'answered':>9}{'right':>7}{'wrong':>7}")
    for field, c in score(golden, results).items():
        print(f"{field:14}{c['labelled']:9}{c['answered']:9}{c['right']:7}{c['wrong']:7}")
    for ad, r in zip(golden, results, strict=True):
        if not r.is_offer or r.warnings:
            print(f"  {ad['id']}: offer={r.is_offer} warnings={r.warnings} | {ad['text'][:70]!r}")
        if r.dropped:
            print(f"  {ad['id']}: dropped {r.dropped}")
        wrong = [
            f"{f}={r.values[f]!r} (form {ad['labels'][f]!r})"
            for f in FIELDS
            if r.values.get(f) is not None
            and ad["labels"].get(f) is not None
            and not same(f, ad["labels"][f], r.values[f])
        ]
        if wrong:
            print(f"  {ad['id']}: wrong {wrong}")
    tokens = [(r.timing.prompt_tokens, r.timing.output_tokens) for r in results]
    p90 = sorted(seconds)[int(0.9 * (len(seconds) - 1))]
    print(
        f"seconds per ad: mean {statistics.mean(seconds):.1f}, p90 {p90:.1f}; tokens per ad:"
        f" prompt {statistics.mean(t[0] for t in tokens):.0f},"
        f" output {statistics.mean(t[1] for t in tokens):.0f}"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="adacar eval")
    parser.add_argument("models", nargs="*", help=f"model ids (default: the tiers): {list(MODELS)}")
    parser.add_argument("--models-dir", type=Path, help="where the models are downloaded")
    parser.add_argument("--server", help="an own OpenAI-compatible server instead")
    parser.add_argument("--server-model", default="local")
    parser.add_argument("--limit", type=int, help="only the first N ads")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    args = parser.parse_args(argv)
    golden = load_golden(args.golden)[: args.limit]

    if args.server:
        with closing(LlmClient(args.server, model=args.server_model, native=False)) as client:
            report(args.server_model, golden, *run_model(client, golden))
        return 0

    hw = hardware.probe()
    models_dir = args.models_dir or paths.models_dir()
    server = LlamaServer(paths.vendor_dir(), paths.log_dir() / "llama-eval.log", idle_seconds=60)
    try:
        for model_id in args.models or list(dict.fromkeys(TIERS.values())):
            spec = MODELS[model_id]
            launch = advisor.launch_for(spec, hw, models_dir / spec.id)
            if not launch.weights.exists():
                print(f"\n== {spec.name}: not downloaded in {models_dir}")
                continue
            with (
                server.use(launch) as url,
                closing(LlmClient(url, model=spec.id, chat_kwargs=spec.chat_kwargs)) as client,
            ):
                report(spec.name, golden, *run_model(client, golden))
    finally:
        server.shutdown()
    return 0
