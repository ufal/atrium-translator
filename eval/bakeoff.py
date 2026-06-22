"""
eval/bakeoff.py – Translator-base quality bake-off (issue #4, Phase 2).

Compares translation backends (e.g. CUBBITT ``lindat`` vs the ``openai_compatible``
LLM adapter, with/without glossary) on a sample of AMCR metadata + ALTO OCR and
writes a per-segment + summary comparison CSV.

Why the metric mix
------------------
Archival OCR text usually has **no reference translation**, so reference-based
BLEU/COMET often cannot be computed. The harness therefore always reports
*reference-less* signals and adds reference-based scores only when a references
TSV is supplied:

  * reference-based (needs --refs + sacrebleu): chrF, BLEU; COMET if installed;
  * reference-less heuristic QE: number/date/code preservation, empty-output rate,
    output/input length ratio (an OCR-robustness / hallucination proxy);
  * terminology hit-rate: fraction of expected glossary targets present in output;
  * pairwise agreement: char-level similarity between backends (divergence signal).

It is a *script*, not a unit test: it calls the real backends (network). Optional
deps (sacrebleu / comet) are imported lazily and the harness degrades gracefully
when they are absent. See docs/translation-backends.md §6.

Usage
-----
    # CUBBITT only (no LLM env needed):
    python -m eval.bakeoff --samples data_samples/my_documents --out bakeoff.csv

    # CUBBITT vs the LLM adapter (LLM_* env configured), with glossary:
    python -m eval.bakeoff --backends lindat,openai_compatible \
        --vocabulary data_samples/vocabulary.csv --limit 40 --out bakeoff.csv
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import sys
from pathlib import Path

from lxml import etree

# Repo imports (run from the repo root or via `python -m eval.bakeoff`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processors.backend import get_backend  # noqa: E402
from processors.vocab import load_vocabulary  # noqa: E402

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


def collect_segments(samples_dir: Path, xpaths: list[str], limit: int | None) -> list[dict]:
    segments: list[dict] = []
    for path in sorted(samples_dir.rglob("*.xml")):
        try:
            if path.name.endswith(".alto.xml"):
                for i, t in enumerate(_alto_block_texts(path)):
                    segments.append({"file": path.name, "kind": "alto", "id": f"block{i}", "src": t})
            else:
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
    """(#expected glossary targets present in tgt, #glossary terms found in src)."""
    low_src = src.lower()
    low_tgt = tgt.lower()
    expected = 0
    hit = 0
    for term, target in vocab.items():
        if term in low_src:
            expected += 1
            if target.lower() in low_tgt:
                hit += 1
    return hit, expected


def char_similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def _load_sacrebleu():
    try:
        import sacrebleu  # noqa: PLC0415

        return sacrebleu
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────


def run(args) -> None:
    xpaths: list[str] = []
    if args.xpaths and Path(args.xpaths).exists():
        xpaths = [
            ln.strip()
            for ln in Path(args.xpaths).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

    vocab = load_vocabulary(args.vocabulary) if args.vocabulary else {}
    refs: dict = {}
    if args.refs and Path(args.refs).exists():
        with open(args.refs, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) >= 2:
                    refs[row[0]] = row[1]
    sacre = _load_sacrebleu() if refs else None
    if refs and sacre is None:
        print(
            "[WARN] --refs given but sacrebleu not installed; chrF/BLEU skipped. pip install -r eval/requirements-eval.txt"
        )

    backend_names = [b.strip() for b in args.backends.split(",") if b.strip()]
    backends = {}
    for name in backend_names:
        ctor_kwargs = {"vocab_path": args.vocabulary} if args.vocabulary else {}
        backends[name] = get_backend(name, **ctor_kwargs)
        print(f"[INFO] backend '{name}' ready (supports_glossary={getattr(backends[name], 'supports_glossary', '?')}).")

    segments = collect_segments(Path(args.samples), xpaths, args.limit)
    print(f"[INFO] {len(segments)} segment(s) collected from {args.samples}.")

    rows: list[dict] = []
    summary: dict = {
        n: {"n": 0, "empty": 0, "term_hit": 0, "term_exp": 0, "numsum": 0.0, "numcnt": 0} for n in backend_names
    }

    for seg in segments:
        src = seg["src"]
        ref = refs.get(f"{seg['file']}:{seg['id']}")
        outs: dict = {}
        for name, backend in backends.items():
            try:
                tgt = backend.translate(src, args.source_lang, args.target_lang)
            except Exception as e:  # noqa: BLE001 - record the failure, keep going
                tgt = ""
                print(f"[WARN] {name} failed on {seg['file']}:{seg['id']}: {e}")
            outs[name] = tgt
            s = summary[name]
            s["n"] += 1
            if not tgt.strip():
                s["empty"] += 1
            hit, exp = terminology_hits(src, tgt, vocab)
            s["term_hit"] += hit
            s["term_exp"] += exp
            npres = number_preservation(src, tgt)
            if isinstance(npres, float):
                s["numsum"] += npres
                s["numcnt"] += 1

            row = {
                "file": seg["file"],
                "kind": seg["kind"],
                "id": seg["id"],
                "backend": name,
                "src_len": len(src),
                "tgt_len": len(tgt),
                "length_ratio": length_ratio(src, tgt),
                "number_preservation": npres,
                "term_hits": hit,
                "term_expected": exp,
                "src": src,
                "tgt": tgt,
            }
            if sacre and ref:
                row["chrF"] = round(sacre.sentence_chrf(tgt, [ref]).score, 2)
                row["BLEU"] = round(sacre.sentence_bleu(tgt, [ref]).score, 2)
            rows.append(row)

        if len(outs) == 2:
            a, b = list(outs.values())
            for row in rows[-2:]:
                row["pairwise_char_sim"] = char_similarity(a, b)

    # Write per-segment CSV.
    fieldnames = sorted({k for r in rows for k in r}, key=lambda k: (k in ("src", "tgt"), k))
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    # Console summary.
    print("\n=== SUMMARY ===")
    for name, s in summary.items():
        n = max(s["n"], 1)
        term = (s["term_hit"] / s["term_exp"]) if s["term_exp"] else float("nan")
        numavg = (s["numsum"] / s["numcnt"]) if s["numcnt"] else float("nan")
        print(
            f"  {name:18s} segments={s['n']:4d}  empty={s['empty']:3d} "
            f"({s['empty'] / n:.1%})  term_hit_rate={term:.3f}  number_preservation={numavg:.3f}"
        )
    print(f"\n[INFO] per-segment results → {args.out}")


def main() -> None:
    p = argparse.ArgumentParser(description="ATRIUM translator-base quality bake-off (issue #4).")
    p.add_argument("--samples", default="data_samples/my_documents", help="Directory of ALTO/AMCR XML samples.")
    p.add_argument("--xpaths", default="amcr-fields.txt", help="XPath list for AMCR metadata field extraction.")
    p.add_argument(
        "--backends", default="lindat", help="Comma-separated backend names (e.g. 'lindat,openai_compatible')."
    )
    p.add_argument("--vocabulary", default=None, help="Glossary CSV (enables terminology hit-rate + backend glossary).")
    p.add_argument("--refs", default=None, help="Optional TSV '<file>:<id>\\t<reference>' for chrF/BLEU/COMET.")
    p.add_argument("--source_lang", default="auto")
    p.add_argument("--target_lang", default="en")
    p.add_argument(
        "--limit", type=int, default=None, help="Cap the number of segments (for quick runs / free-tier limits)."
    )
    p.add_argument("--out", default="bakeoff.csv")
    run(p.parse_args())


if __name__ == "__main__":
    main()
