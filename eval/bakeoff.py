"""
eval/bakeoff.py – Translator-base quality bake-off (issue #4, Phase 2).

Compares translation backends (CUBBITT ``lindat``, the ``openai_compatible`` LLM
adapter, the ``ct2`` CTranslate2 self-host backend; with/without glossary) on a
sample of AMCR metadata + ALTO OCR and writes two CSVs:

  * ``--out`` — one row per (segment, backend): output, per-segment scores;
  * ``--summary-out`` (default ``<out>_summary.csv``) — one row per backend:
    ``segments``, ``failed``, ``empty`` / ``empty_rate``, ``term_hits`` /
    ``term_expected`` / ``term_hit_rate``, mean ``number_preservation`` and
    ``length_ratio``, mean ``baseline_char_sim``, ``refs_used``, corpus ``chrF``
    / ``BLEU``, ``COMET`` / ``COMET_QE`` system scores, and the time the backend
    took: ``seconds`` and ``chars_per_s`` (source characters per second);
  * ``<out>_by_kind.csv`` — the same per backend and segment kind (``metadata``,
    ``alto``), so the AMČR fields and the OCR slice are never averaged together;
  * ``<out>.meta.json`` — what ran: each backend's ``describe()`` (model, device,
    quantisation; never a key), the vocabulary's sha256, the commit, warm-up time.

One variant per run, combined afterwards
----------------------------------------
On a cluster each variant usually needs its own environment (a ``ct2`` model per
``CT2_MODEL_DIR``, an LLM server per ``LLM_*``, network for ``lindat``), so the
bake-off runs one variant at a time, named with ``--label``, and ``--combine``
merges the per-segment CSVs: it recomputes ``baseline_char_sim`` against the
first file's variant and writes the three outputs above for all of them.

Why the metric mix
------------------
Archival OCR text usually has **no reference translation**, so reference-based
scores often cannot be computed. The harness therefore always reports
*reference-less* signals and adds reference-based scores only when a references
TSV is supplied:

  * reference-based (needs ``--refs``): chrF and BLEU (sacrebleu), per segment and
    corpus-level; COMET (unbabel-comet, ``--comet-model``, default
    ``Unbabel/wmt22-comet-da``), per segment and system-level;
  * reference-free neural QE, opt-in: COMET-QE with ``--comet-qe-model`` (e.g.
    ``Unbabel/wmt22-cometkiwi-da``, a gated Hugging Face model);
  * reference-less heuristic QE: number/date/code preservation, empty-output rate,
    output/input length ratio (an OCR-robustness / hallucination proxy);
  * terminology hit-rate: fraction of expected glossary targets present in output;
  * divergence: char-level similarity of each backend's output to the output of
    the first backend in ``--backends`` (the baseline), for any number of backends.

``failed`` counts segments where the backend raised; ``empty`` counts segments
where it answered with blank text. They are kept apart because they mean
different things (an outage or refusal vs. a model that dropped the content).
The heuristic columns (``number_preservation``, ``length_ratio``, terminology,
``baseline_char_sim``) describe the outputs a backend actually produced, so
failed segments are left out of them. The reference-based system scores are
computed on the same segment set for every backend, so there a failed segment
scores as an empty hypothesis.

It is a *script*, not a unit test: it calls the real backends (network). Optional
deps (sacrebleu / unbabel-comet) are imported lazily and the harness degrades to
a warning when they are absent. See "Suggested next steps" (under "Recommendation")
in docs/translation-backends.md.

The source language must be explicit (``--source_lang cs``, the default): the
backends receive it as is, and the pipeline's language detection is not part of
what is compared.

Usage
-----
    # CUBBITT only (no LLM env needed):
    python -m eval.bakeoff --samples data_samples/my_documents --out bakeoff.csv

    # CUBBITT vs the LLM adapter (LLM_* env configured), with glossary:
    python -m eval.bakeoff --backends lindat,openai_compatible \
        --vocabulary data_samples/vocabulary.csv --limit 40 --out bakeoff.csv

    # The planned three-way bake-off (LLM_* and CT2_* env configured), with
    # references for chrF/BLEU/COMET, plus COMET-QE on a GPU:
    python -m eval.bakeoff --backends lindat,openai_compatible,ct2 \
        --vocabulary data_samples/vocabulary.csv --refs refs.tsv \
        --comet-qe-model Unbabel/wmt22-cometkiwi-da --comet-gpus 1 \
        --limit 40 --out bakeoff.csv

    # One variant per run (its own env), then combine:
    CT2_MODEL_DIR=… python -m eval.bakeoff --backends ct2 --label ct2-eurollm-9b \
        --vocabulary data_samples/vocabulary.csv --out runs/ct2-eurollm-9b.csv
    python -m eval.bakeoff --combine runs/lindat.csv runs/ct2-eurollm-9b.csv \
        --out bakeoff.csv
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

# Repo imports (run from the repo root or via `python -m eval.bakeoff`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processors.backend import get_backend  # noqa: E402
from processors.vocab import get_matching_terms, load_vocabulary  # noqa: E402

_SECURE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)

# Number / code / date-like tokens whose preservation is a faithfulness signal.
_NUM_RE = re.compile(r"\d[\d.,/:°'\"-]*\d|\d")


# ──────────────────────────────────────────────────────────────────────────────
# Segment extraction
# ──────────────────────────────────────────────────────────────────────────────


def _alto_block_texts(path: Path, ns_key: str = "alto") -> list[str]:
    tree = etree.parse(str(path), parser=_SECURE_PARSER)
    root = tree.getroot()
    nsmap = root.nsmap
    ns = {ns_key: nsmap[None]} if None in nsmap else nsmap
    use_ns = ns_key in ns
    blocks = root.xpath(f"//{ns_key}:TextBlock", namespaces=ns) if use_ns else root.xpath("//TextBlock")
    out: list[str] = []
    for block in blocks:
        lines = block.xpath(f".//{ns_key}:TextLine", namespaces=ns) if use_ns else block.xpath(".//TextLine")
        texts = []
        for line in lines:
            strings = line.xpath(f".//{ns_key}:String", namespaces=ns) if use_ns else line.xpath(".//String")
            texts.append(" ".join(s.get("CONTENT", "") for s in strings if s.get("CONTENT")).strip())
        block_text = " ".join(t for t in texts if t).strip()
        if block_text:
            out.append(block_text)
    return out


def _metadata_field_texts(path: Path, xpaths: list[str]) -> list[str]:
    tree = etree.parse(str(path), parser=_SECURE_PARSER)
    root = tree.getroot()
    ns: dict = {}
    for elem in root.iter():
        for _prefix, uri in (elem.nsmap or {}).items():
            if uri and "amcr" in uri:
                ns.setdefault("amcr", uri)
            if uri and "OAI-PMH" in uri:
                ns.setdefault("oai", uri)
    out: list[str] = []
    for xp in xpaths:
        try:
            for elem in root.xpath(xp, namespaces=ns):
                if elem.text and elem.text.strip():
                    out.append(elem.text.strip())
        except etree.XPathError:
            continue
    return out


#: The segment kinds collect_segments() produces.
KINDS = ("metadata", "alto")


def collect_segments(
    samples_dir: Path, xpaths: list[str], limit: int | None, kinds: tuple[str, ...] = KINDS
) -> list[dict]:
    segments: list[dict] = []
    for path in sorted(samples_dir.rglob("*.xml")):
        try:
            if path.name.endswith(".alto.xml"):
                if "alto" not in kinds:
                    continue
                for i, t in enumerate(_alto_block_texts(path)):
                    segments.append({"file": path.name, "kind": "alto", "id": f"block{i}", "src": t})
            elif "metadata" in kinds:
                for i, t in enumerate(_metadata_field_texts(path, xpaths)):
                    segments.append({"file": path.name, "kind": "metadata", "id": f"field{i}", "src": t})
        except Exception as e:  # noqa: BLE001 - a malformed sample must not abort the run
            print(f"[WARN] could not read {path.name}: {e}")
        if limit and len(segments) >= limit:
            return segments[:limit]
    return segments


# ──────────────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────────────


def number_preservation(src: str, tgt: str) -> float | str:
    src_nums = _NUM_RE.findall(src)
    if not src_nums:
        return ""  # not applicable
    kept = sum(1 for n in src_nums if n in tgt)
    return round(kept / len(src_nums), 4)


def length_ratio(src: str, tgt: str) -> float | str:
    s = len(src.strip())
    return round(len(tgt.strip()) / s, 4) if s else ""


def terminology_hits(src: str, tgt: str, vocab: dict) -> tuple[int, int]:
    """(#expected glossary targets present in tgt, #glossary terms found in src).

    A term counts as found in *src* as a whole word, the way the backends match it
    (``get_matching_terms``): "kost" is not expected inside "kostel".
    """
    low_tgt = tgt.lower()
    matched = get_matching_terms(src, vocab)
    hit = sum(1 for _term, target in matched if target.lower() in low_tgt)
    return hit, len(matched)


def char_similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def _load_sacrebleu():
    try:
        import sacrebleu  # noqa: PLC0415

        return sacrebleu
    except Exception:
        return None


def _load_comet():
    """The ``comet`` module from unbabel-comet, or ``None`` when not installed."""
    try:
        import comet  # noqa: PLC0415

        return comet
    except Exception:
        return None


def comet_scores(comet_mod, model_name: str, data: list[dict], gpus: int = 0) -> tuple[list[float], float]:
    """Score *data* (``[{"src", "mt"[, "ref"]}]``) with a COMET checkpoint.

    Returns ``(per-segment scores, system score)``. The same call serves
    reference-based COMET (items carry ``ref``) and COMET-QE (they do not).
    """
    model = comet_mod.load_from_checkpoint(comet_mod.download_model(model_name))
    out = model.predict(data, batch_size=8, gpus=gpus)
    return [float(x) for x in out.scores], float(out.system_score)


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

#: Column order of the summary CSV (one row per backend).
SUMMARY_COLUMNS = (
    "backend",
    "segments",
    "failed",
    "empty",
    "empty_rate",
    "term_hits",
    "term_expected",
    "term_hit_rate",
    "number_preservation",
    "length_ratio",
    "baseline",
    "baseline_char_sim",
    "refs_used",
    "chrF",
    "BLEU",
    "COMET",
    "COMET_QE",
    "seconds",
    "chars_per_s",
)


def _load_xpaths(path: str | None) -> list[str]:
    if not path or not Path(path).exists():
        return []
    return [
        ln.strip()
        for ln in Path(path).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def _load_refs(path: str | None) -> dict[str, str]:
    """``{"<file>:<id>": reference}`` from a two-column TSV; ``{}`` without one."""
    refs: dict[str, str] = {}
    if not path:
        return refs
    if not Path(path).exists():
        print(f"[WARN] --refs {path} does not exist; reference-based scores skipped.")
        return refs
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) >= 2:
                refs[row[0]] = row[1]
    return refs


def _mean(values: list[float]) -> float | str:
    return round(sum(values) / len(values), 4) if values else ""


def summarise(rows: list[dict], backends: list[str], *, sacre=None, comet_system: dict | None = None) -> list[dict]:
    """One summary row per backend (in *backends* order) from per-segment *rows*.

    Pure: no I/O. *sacre* (the sacrebleu module) enables corpus chrF/BLEU over
    rows carrying a ``ref``; *comet_system* maps ``"COMET"`` / ``"COMET_QE"`` to
    ``{backend: system score}``. See the module docstring for which rows each
    column counts.
    """
    baseline = backends[0] if backends else ""
    comet_system = comet_system or {}
    summary: list[dict] = []
    for name in backends:
        mine = [r for r in rows if r["backend"] == name]
        answered = [r for r in mine if not r["failed"]]
        empty = sum(1 for r in answered if not r["tgt"].strip())
        term_hits = sum(r["term_hits"] for r in answered)
        term_expected = sum(r["term_expected"] for r in answered)
        sims = [r["baseline_char_sim"] for r in answered if isinstance(r.get("baseline_char_sim"), float)]
        with_ref = [r for r in mine if r.get("ref")]
        entry = {
            "backend": name,
            "segments": len(mine),
            "failed": len(mine) - len(answered),
            "empty": empty,
            "empty_rate": round(empty / len(mine), 4) if mine else "",
            "term_hits": term_hits,
            "term_expected": term_expected,
            "term_hit_rate": round(term_hits / term_expected, 4) if term_expected else "",
            "number_preservation": _mean(
                [r["number_preservation"] for r in answered if isinstance(r["number_preservation"], float)]
            ),
            "length_ratio": _mean([r["length_ratio"] for r in answered if isinstance(r["length_ratio"], float)]),
            "baseline": baseline,
            "baseline_char_sim": _mean(sims) if name != baseline else "",
            "refs_used": len(with_ref),
            "chrF": "",
            "BLEU": "",
            "COMET": round(comet_system["COMET"][name], 4) if name in comet_system.get("COMET", {}) else "",
            "COMET_QE": round(comet_system["COMET_QE"][name], 4) if name in comet_system.get("COMET_QE", {}) else "",
        }
        if sacre is not None and with_ref:
            hyps = [r["tgt"] for r in with_ref]
            refs = [[r["ref"] for r in with_ref]]
            entry["chrF"] = round(sacre.corpus_chrf(hyps, refs).score, 2)
            entry["BLEU"] = round(sacre.corpus_bleu(hyps, refs).score, 2)
        timed = [r for r in mine if isinstance(r.get("seconds"), float)]
        seconds = sum(r["seconds"] for r in timed)
        entry["seconds"] = round(seconds, 2) if timed else ""
        chars = sum(r["src_len"] for r in timed if not r["failed"])
        entry["chars_per_s"] = round(chars / seconds, 1) if timed and seconds > 0 else ""
        summary.append(entry)
    return summary


def summarise_by_kind(rows: list[dict], backends: list[str], **kwargs) -> list[dict]:
    """:func:`summarise` per segment kind: one row per (backend, kind) present, ``kind`` first.

    The AMČR metadata fields and the OCR blocks are different tasks, so the report never
    averages them together. The baseline divergence is the per-segment value, unchanged.
    """
    out: list[dict] = []
    for kind in KINDS:
        subset = [r for r in rows if r.get("kind") == kind]
        if not subset:
            continue
        present = [name for name in backends if any(r["backend"] == name for r in subset)]
        out.extend({"kind": kind, **entry} for entry in summarise(subset, present, **kwargs))
    return out


def _write_csv(path, rows: list[dict], fieldnames=None) -> None:
    if fieldnames is None:
        # Per-segment CSV: scores alphabetically, the long text columns last.
        fieldnames = sorted({k for r in rows for k in r}, key=lambda k: (k in ("src", "tgt", "ref", "error"), k))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fieldnames))
        w.writeheader()
        w.writerows(rows)


def _default_summary_path(out: str) -> Path:
    p = Path(out)
    return p.with_name(f"{p.stem}_summary{p.suffix or '.csv'}")


def _by_kind_path(out: str) -> Path:
    p = Path(out)
    return p.with_name(f"{p.stem}_by_kind{p.suffix or '.csv'}")


def _meta_path(out: str) -> Path:
    p = Path(out)
    return p.with_name(f"{p.stem}.meta.json")


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance nicety, never fatal
        return ""


def _sha256(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _write_meta(out: str, meta: dict) -> Path:
    path = _meta_path(out)
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def _print_summary(summary: list[dict]) -> None:
    print("\n=== SUMMARY ===")
    for s in summary:
        extras = "".join(
            f"  {k}={s[k]}"
            for k in ("baseline_char_sim", "chrF", "BLEU", "COMET", "COMET_QE", "seconds", "chars_per_s")
            if s[k] != ""
        )
        print(
            f"  {s['backend']:18s} segments={s['segments']:4d}  failed={s['failed']:3d}  empty={s['empty']:3d} "
            f"({s['empty_rate'] or 0:.1%})  term_hit_rate={s['term_hit_rate']}  "
            f"number_preservation={s['number_preservation']}{extras}"
        )


def _score_and_write(rows: list[dict], backend_names: list[str], args, *, sacre, comet_mod, want_comet: bool):
    """COMET passes (optional), then the per-segment, summary and per-kind CSVs; returns the summary."""
    comet_model = (getattr(args, "comet_model", "") or "").strip()
    comet_qe_model = (getattr(args, "comet_qe_model", "") or "").strip()
    comet_system: dict[str, dict] = {}
    if comet_mod is not None:
        gpus = int(getattr(args, "comet_gpus", 0) or 0)
        if want_comet:
            comet_system["COMET"] = _add_comet(rows, "COMET", comet_mod, comet_model, gpus, with_ref=True)
        if comet_qe_model:
            comet_system["COMET_QE"] = _add_comet(rows, "COMET_QE", comet_mod, comet_qe_model, gpus, with_ref=False)

    summary = summarise(rows, backend_names, sacre=sacre, comet_system=comet_system)
    by_kind = summarise_by_kind(rows, backend_names, sacre=sacre)
    summary_out = Path(args.summary_out) if getattr(args, "summary_out", None) else _default_summary_path(args.out)
    _write_csv(args.out, rows)
    _write_csv(summary_out, summary, SUMMARY_COLUMNS)
    _write_csv(_by_kind_path(args.out), by_kind, ("kind", *SUMMARY_COLUMNS))
    _print_summary(summary)
    print(f"\n[INFO] per-segment results → {args.out}")
    print(f"[INFO] per-backend summary → {summary_out}")
    print(f"[INFO] per-backend, per-kind summary → {_by_kind_path(args.out)}")
    return summary


def _add_comet(rows: list[dict], column: str, comet_mod, model_name: str, gpus: int, with_ref: bool) -> dict:
    """Score *rows* with one COMET model per backend; return ``{backend: system}``.

    Reference-based COMET scores the rows that carry a ``ref``; COMET-QE scores
    every row. A failed segment is scored as an empty hypothesis. Any error
    (download, gated model, CUDA) degrades to a warning so the CSVs are still
    written after the expensive translation pass.
    """
    by_backend: dict[str, list[dict]] = {}
    for r in rows:
        if with_ref and not r.get("ref"):
            continue
        by_backend.setdefault(r["backend"], []).append(r)
    system: dict[str, float] = {}
    for name, subset in by_backend.items():
        data = [{"src": r["src"], "mt": r["tgt"], **({"ref": r["ref"]} if with_ref else {})} for r in subset]
        try:
            seg_scores, sys_score = comet_scores(comet_mod, model_name, data, gpus)
        except Exception as e:  # noqa: BLE001 - optional metric, never fatal
            print(f"[WARN] {column} ({model_name}) failed for backend '{name}': {e}")
            continue
        for r, score in zip(subset, seg_scores):
            r[column] = round(score, 4)
        system[name] = sys_score
    return system


def run(args) -> list[dict]:
    """Translate, score and write both CSVs; return the summary rows."""
    xpaths = _load_xpaths(args.xpaths)
    vocab = load_vocabulary(args.vocabulary) if args.vocabulary else {}
    refs = _load_refs(args.refs)
    sacre = _load_sacrebleu() if refs else None
    if refs and sacre is None:
        print(
            "[WARN] --refs given but sacrebleu not installed; chrF/BLEU skipped. pip install -r eval/requirements-eval.txt"
        )

    comet_model = (getattr(args, "comet_model", "") or "").strip()
    comet_qe_model = (getattr(args, "comet_qe_model", "") or "").strip()
    want_comet = bool(refs and comet_model)
    comet_mod = _load_comet() if (want_comet or comet_qe_model) else None
    if (want_comet or comet_qe_model) and comet_mod is None:
        print("[WARN] unbabel-comet not installed; COMET/COMET-QE skipped. pip install -r eval/requirements-eval.txt")

    started = datetime.now(timezone.utc)
    names = [b.strip() for b in args.backends.split(",") if b.strip()]
    label = (getattr(args, "label", None) or "").strip()
    backends = {}
    described: dict[str, dict] = {}
    warm_s: dict[str, float] = {}
    for name in names:
        ctor_kwargs = {"vocab_path": args.vocabulary} if args.vocabulary else {}
        backend = get_backend(name, **ctor_kwargs)
        column = label or name  # the `backend` column: the variant's label when one is given
        warm = getattr(backend, "warm", None)
        if callable(warm):  # load the model before the clock starts (ct2)
            t0 = time.perf_counter()
            warm()
            warm_s[column] = round(time.perf_counter() - t0, 2)
        describe = getattr(backend, "describe", None)
        described[column] = {"backend": name, **(describe() if callable(describe) else {})}
        backends[column] = backend
        print(f"[INFO] backend '{column}' ready (supports_glossary={getattr(backend, 'supports_glossary', '?')}).")
    backend_names = list(backends)
    baseline = backend_names[0] if backend_names else ""

    kinds = tuple(k.strip() for k in (getattr(args, "kinds", None) or ",".join(KINDS)).split(",") if k.strip())
    segments = collect_segments(Path(args.samples), xpaths, args.limit, kinds)
    print(f"[INFO] {len(segments)} segment(s) collected from {args.samples}.")

    rows: list[dict] = []
    for seg in segments:
        src = seg["src"]
        ref = refs.get(f"{seg['file']}:{seg['id']}")
        seg_rows: dict[str, dict] = {}
        for name, backend in backends.items():
            error = ""
            t0 = time.perf_counter()
            try:
                tgt = backend.translate(src, args.source_lang, args.target_lang)
            except Exception as e:  # noqa: BLE001 - record the failure, keep going
                tgt = ""
                error = str(e)
                print(f"[WARN] {name} failed on {seg['file']}:{seg['id']}: {e}")
            seconds = round(time.perf_counter() - t0, 3)
            tgt = tgt or ""
            hit, exp = terminology_hits(src, tgt, vocab)
            row = {
                "file": seg["file"],
                "kind": seg["kind"],
                "id": seg["id"],
                "backend": name,
                "failed": 1 if error else 0,
                "src_len": len(src),
                "tgt_len": len(tgt),
                # A failed call produced no output to measure: blank, not 0.0.
                "length_ratio": "" if error else length_ratio(src, tgt),
                "number_preservation": "" if error else number_preservation(src, tgt),
                "term_hits": hit,
                "term_expected": exp,
                "baseline_char_sim": "",
                "seconds": seconds,
                "src": src,
                "tgt": tgt,
                "error": error,
            }
            if ref:
                row["ref"] = ref
                if sacre:
                    row["chrF"] = round(sacre.sentence_chrf(tgt, [ref]).score, 2)
                    row["BLEU"] = round(sacre.sentence_bleu(tgt, [ref]).score, 2)
            seg_rows[name] = row
            rows.append(row)

        # Divergence from the baseline backend, for any number of backends.
        base = seg_rows.get(baseline)
        if base is not None and not base["failed"]:
            for name, row in seg_rows.items():
                if name != baseline and not row["failed"]:
                    row["baseline_char_sim"] = char_similarity(base["tgt"], row["tgt"])

    summary = _score_and_write(rows, backend_names, args, sacre=sacre, comet_mod=comet_mod, want_comet=want_comet)
    meta = {
        "variants": described,
        "warm_seconds": warm_s,
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "samples": str(args.samples),
        "kinds": list(kinds),
        "limit": args.limit,
        "segments": len(segments),
        "vocabulary": str(args.vocabulary or ""),
        "vocabulary_sha256": _sha256(args.vocabulary) if args.vocabulary else "",
        "commit": _git_commit(),
        "python": platform.python_version(),
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"[INFO] what ran → {_write_meta(args.out, meta)}")
    return summary


#: Per-segment columns read back as numbers by --combine (the rest stay text).
_INT_COLUMNS = ("failed", "src_len", "tgt_len", "term_hits", "term_expected")
_FLOAT_COLUMNS = ("length_ratio", "number_preservation", "baseline_char_sim", "seconds", "chrF", "BLEU")


def _typed(row: dict) -> dict:
    """A per-segment row as read from CSV, with its numeric columns numeric again ("" stays "")."""
    out = dict(row)
    for key in _INT_COLUMNS:
        out[key] = int(out.get(key) or 0)
    for key in _FLOAT_COLUMNS:
        value = out.get(key, "")
        out[key] = float(value) if value not in ("", None) else ""
    return out


def combine(args) -> list[dict]:
    """Merge per-variant per-segment CSVs (``--combine``) into one bake-off.

    The variants keep their order: the first file's variant is the baseline. Divergence
    from it is recomputed segment by segment (``file``, ``id``); COMET passes run if asked.
    """
    rows: list[dict] = []
    names: list[str] = []
    for path in args.combine:
        with open(path, encoding="utf-8", newline="") as fh:
            part = [_typed(r) for r in csv.DictReader(fh)]
        if not part:
            print(f"[WARN] {path} has no rows; left out.")
            continue
        for name in dict.fromkeys(r["backend"] for r in part):
            if name in names:
                raise SystemExit(
                    f"[ERROR] variant {name!r} appears in more than one file; give each run its own --label."
                )
            names.append(name)
        rows.extend(part)
    if not rows:
        raise SystemExit("[ERROR] --combine found no rows to merge.")
    baseline = names[0]
    base = {(r["file"], r["id"]): r for r in rows if r["backend"] == baseline}
    for row in rows:
        row["baseline_char_sim"] = ""
        ref = base.get((row["file"], row["id"]))
        if row["backend"] != baseline and ref is not None and not ref["failed"] and not row["failed"]:
            row["baseline_char_sim"] = char_similarity(ref["tgt"], row["tgt"])

    with_refs = any(r.get("ref") for r in rows)
    sacre = _load_sacrebleu() if with_refs else None
    comet_model = (getattr(args, "comet_model", "") or "").strip()
    comet_qe_model = (getattr(args, "comet_qe_model", "") or "").strip()
    want_comet = bool(with_refs and comet_model)
    comet_mod = _load_comet() if (want_comet or comet_qe_model) else None
    if (want_comet or comet_qe_model) and comet_mod is None:
        print("[WARN] unbabel-comet not installed; COMET/COMET-QE skipped. pip install -r eval/requirements-eval.txt")
    summary = _score_and_write(rows, names, args, sacre=sacre, comet_mod=comet_mod, want_comet=want_comet)
    metas = {}
    for path in args.combine:
        meta_path = _meta_path(path)
        if meta_path.is_file():
            metas[str(path)] = json.loads(meta_path.read_text(encoding="utf-8"))
    print(f"[INFO] what ran → {_write_meta(args.out, {'combined': list(map(str, args.combine)), 'runs': metas})}")
    return summary


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="ATRIUM translator-base quality bake-off (issue #4).")
    p.add_argument("--samples", default="data_samples/my_documents", help="Directory of ALTO/AMCR XML samples.")
    p.add_argument("--xpaths", default="amcr-fields.txt", help="XPath list for AMCR metadata field extraction.")
    p.add_argument(
        "--backends",
        default="lindat",
        help="Comma-separated backend names (e.g. 'lindat,openai_compatible,ct2'); the first is the "
        "baseline for baseline_char_sim.",
    )
    p.add_argument("--vocabulary", default=None, help="Glossary CSV (enables terminology hit-rate + backend glossary).")
    p.add_argument("--refs", default=None, help="Optional TSV '<file>:<id>\\t<reference>' for chrF/BLEU/COMET.")
    p.add_argument(
        "--source_lang",
        default="cs",
        help="The source language every backend receives (default cs). 'auto' is refused: language detection is "
        "the pipeline's, not part of what is compared.",
    )
    p.add_argument("--target_lang", default="en")
    p.add_argument(
        "--label",
        default=None,
        help="Name this run's variant in the `backend` column (e.g. ct2-eurollm-9b, llm-desc300); one backend only.",
    )
    p.add_argument(
        "--kinds", default=",".join(KINDS), help="Segment kinds to collect: metadata, alto or both (default)."
    )
    p.add_argument(
        "--combine",
        nargs="+",
        metavar="CSV",
        default=None,
        help="Merge per-segment CSVs of earlier runs (the first is the baseline) instead of translating.",
    )
    p.add_argument(
        "--limit", type=int, default=None, help="Cap the number of segments (for quick runs / free-tier limits)."
    )
    p.add_argument("--out", default="bakeoff.csv", help="Per-segment CSV.")
    p.add_argument("--summary-out", default=None, help="Per-backend summary CSV (default: <out>_summary.csv).")
    p.add_argument(
        "--comet-model",
        default="Unbabel/wmt22-comet-da",
        help="Reference-based COMET checkpoint, used when --refs is given ('' disables).",
    )
    p.add_argument(
        "--comet-qe-model",
        default=None,
        help="Reference-free COMET-QE checkpoint (opt-in), e.g. Unbabel/wmt22-cometkiwi-da.",
    )
    p.add_argument("--comet-gpus", type=int, default=0, help="GPUs for COMET/COMET-QE scoring (0 = CPU).")
    args = p.parse_args(argv)
    if args.combine:
        combine(args)
        return
    if str(args.source_lang).strip().lower() == "auto":
        p.error("--source_lang auto is not supported: give the language every backend receives (e.g. cs)")
    if args.label and len([b for b in args.backends.split(",") if b.strip()]) != 1:
        p.error("--label names ONE variant: pass a single --backends value with it")
    unknown = sorted(set(k.strip() for k in args.kinds.split(",") if k.strip()) - set(KINDS))
    if unknown:
        p.error(f"--kinds: unknown kind(s) {unknown}; choose from {', '.join(KINDS)}")
    run(args)


if __name__ == "__main__":
    main()
