"""
utils.py – ALTO and metadata XML processing utilities for the ATRIUM translation pipeline.

Security note (review finding #2)
---------------------------------
Every input document is parsed with ``_SECURE_PARSER``, which disables external
entity resolution and network access for DTDs and caps tree size, so the tool is
safe to point at untrusted / semi-trusted XML (including files fetched by URL).
XSD schema documents (an explicit, trusted ``--xsd`` input) are parsed with
``_XSD_PARSER``, which still disables entity resolution but permits the network
access that ``xs:import``-based schemas may require.
"""

import difflib
import logging
import os
import sys
import time
import urllib.request

from lxml import etree

from atrium_document import canonical_doc_id
from processors.language import (
    DOCUMENT_SAMPLE_CHARS,
    LanguageTally,
    SourceLanguagePolicy,
    allowed_source_languages,
    resolve_source_language,
)
from processors.quality import degeneration_reason
from processors.translator import DegenerateTranslationError

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Output mode (issue #46 — the "replace vs append" question)
# ──────────────────────────────────────────────────────────────────────────────
#
# REPLACE is what this tool has always done and stays the default: the source
# text node is overwritten, so the artifact is a monolingual mirror of the input
# and the Czech survives only in the sidecar `_log.csv`.
#
# APPEND keeps the source and adds the translation beside it. For AMCR metadata
# that is a sibling element distinguished by `xml:lang` — the shape AMCR's own
# thesaurus already uses for `heslo` / `heslo_en`, which `load_vocab.py` reads on
# every glossary build. See `agent_dev_logs/digests/46.digest.md`.
#
# The two modes are a genuine fork in the OUTPUT CONTRACT, not a formatting
# preference, which is why the choice is recorded in paradata and in the document
# record's `translations` block rather than being left implicit.
OUTPUT_MODE_REPLACE = "replace"
OUTPUT_MODE_APPEND = "append"
OUTPUT_MODES = (OUTPUT_MODE_REPLACE, OUTPUT_MODE_APPEND)
DEFAULT_OUTPUT_MODE = OUTPUT_MODE_REPLACE

#: The XML-namespace `lang` attribute in Clark notation. `xml:lang` is a GLOBAL
#: attribute from the XML namespace rather than anything AMCR declares, and the
#: AMCR corpus already carries it on 23 controlled-vocabulary element types — so
#: the schema's import of the XML namespace demonstrably exists. Whether the
#: schema also permits the repeated ELEMENT that append mode emits is the open
#: `maxOccurs` question (#46): this code does not guess, it emits the pair and
#: lets `--xsd` report.
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def normalize_output_mode(value, *, source="output mode"):
    """Coerce *value* to a member of :data:`OUTPUT_MODES`.

    Falls back to the default with a warning rather than raising: an unreadable
    mode string arriving from a config file or an env var should not take a batch
    run down mid-corpus, and the effective mode is recorded in paradata either
    way, so a run is never ambiguous about what it produced.
    """
    if value is None:
        return DEFAULT_OUTPUT_MODE
    candidate = str(value).strip().lower()
    if not candidate:
        return DEFAULT_OUTPUT_MODE
    if candidate not in OUTPUT_MODES:
        logger.warning(
            "Unknown %s %r; falling back to %r (valid: %s).",
            source,
            value,
            DEFAULT_OUTPUT_MODE,
            ", ".join(OUTPUT_MODES),
        )
        return DEFAULT_OUTPUT_MODE
    return candidate


class BatchFallbackCounter:
    """Counts how often page-level batching degraded, and what happened to the segments.

    `_translate_items` sends a whole page's blocks (and separately its lines) as
    ONE newline-joined request, then reverts to one request per item when the
    reply does not come back with the same number of lines — or, since the
    2026-09-26 regression, when any item in it is implausible. The difference is
    roughly 2 calls per page versus one per block plus one per line — for the
    79-page sample in `data_samples/`, ~158 calls against ~3288 — and until this
    counter existed nothing distinguished the two. A run that took twenty minutes
    and a run that took one looked identical in the logs.

    That matters most for the in-production experiment (#46): "it was slow" is not
    actionable feedback, "it fell back on 61 of 79 pages because CUBBITT collapsed
    the newlines" is. The segment counts say what the degenerate-output guard did:
    how many blocks / line anchors were flagged, how many the end-of-document
    re-run recovered, and how many were left as source text.
    """

    __slots__ = (
        "batched",
        "fallback_mismatch",
        "fallback_error",
        "fallback_implausible",
        "items_retried",
        "segments_flagged",
        "segments_recovered",
        "segments_untranslated",
        "anchors_flagged",
        "anchors_recovered",
        "anchors_approximated",
    )

    def __init__(self):
        for name in self.__slots__:
            setattr(self, name, 0)

    @property
    def fallbacks(self) -> int:
        return self.fallback_mismatch + self.fallback_error + self.fallback_implausible

    @property
    def needs_attention(self) -> bool:
        """True when anything other than clean batching happened (→ WARNING summary)."""
        return bool(self.fallbacks or self.segments_flagged or self.anchors_flagged or self.anchors_approximated)

    def as_dict(self) -> dict:
        data = {name: getattr(self, name) for name in self.__slots__}
        data["fallbacks"] = self.fallbacks
        return data

    def summary(self) -> str:
        total = self.batched + self.fallbacks
        parts = [
            f"{self.batched}/{total} batch calls were accepted as sent; "
            f"{self.fallbacks} fell back to per-item requests "
            f"({self.fallback_mismatch} line-count mismatch, {self.fallback_error} transport error, "
            f"{self.fallback_implausible} implausible reply), "
            f"costing {self.items_retried} extra requests"
        ]
        if self.segments_flagged or self.anchors_flagged:
            parts.append(
                f"{self.segments_flagged} segment(s) and {self.anchors_flagged} line anchor(s) flagged for the "
                f"end-of-document re-run, {self.segments_recovered} segment(s) and "
                f"{self.anchors_recovered} anchor(s) recovered"
            )
        if self.segments_untranslated:
            parts.append(f"{self.segments_untranslated} segment(s) left untranslated (source text kept)")
        if self.anchors_approximated:
            parts.append(f"{self.anchors_approximated} line(s) placed by source word count (anchor unusable)")
        return "; ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Degenerate output: validation, flagging and the end-of-document re-run
# ──────────────────────────────────────────────────────────────────────────────
#
# On 2026-09-26 roughly a third of all LINDAT replies came back as one word
# repeated up to ~150 times, nondeterministically. Only the line count of a
# reply was ever checked, so the garbage went into the XML and the CSV, and —
# through the ALTO line anchors, which are never logged — into the word-to-box
# alignment of every block on the page. Every translated segment now goes
# through `degeneration_reason`; one that is still unusable after the backend's
# own retries is FLAGGED, re-run once the whole document has been processed, and
# kept as source text if it never recovers. See agent_dev_logs/digests/46.digest.md.

#: Per-line status written to the last column of the `_log.csv`.
STATUS_OK = "ok"
#: Flagged during processing and recovered by the end-of-document re-run.
STATUS_RERUN = "rerun"
#: ALTO only: the line's anchor was unusable, so its words were placed by source
#: word count instead of by similarity to its own translation.
STATUS_APPROX = "approx_alignment"
#: Still degenerate after the re-run: the source text was kept, target left empty.
STATUS_UNTRANSLATED = "untranslated"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _rerun_policy() -> tuple[int, float]:
    """``(rounds, cool-down seconds)`` for re-running flagged segments.

    Read per document rather than at import, so a long-running service picks up a
    changed value and a test can set it. ``TRANSLATION_RERUN_ROUNDS=0`` disables
    the re-run: flagged segments are then kept as source straight away.
    """
    rounds = max(0, _env_int("TRANSLATION_RERUN_ROUNDS", 1))
    delay_s = max(0.0, _env_float("TRANSLATION_RERUN_DELAY_S", 10.0))
    return rounds, delay_s


def _translate_one(translator, text, src_lang, tgt_lang):
    """Translate ONE segment. Returns ``(translation, None)`` or ``(None, reason)``.

    ``None`` means the backend could not produce a usable translation: it raised
    :class:`DegenerateTranslationError` (after its own retries), or the reply fails
    :func:`degeneration_reason` — which also covers backends and test doubles with
    no guard of their own. Any other exception propagates unchanged: a transport
    failure still skips the whole file, exactly as before.
    """
    try:
        translated = translator.translate(text, src_lang, tgt_lang)
    except DegenerateTranslationError as exc:
        return None, str(exc)
    if not isinstance(translated, str):
        return None, f"backend returned {type(translated).__name__}, not text"
    reason = degeneration_reason(text, translated)
    if reason:
        return None, reason
    return translated, None


def _translate_items(translator, texts, lang, tgt_lang, counter):
    """Translate *texts* as ONE newline-joined request; returns ``(results, failed)``.

    ``results`` has one entry per input (``""`` for blank inputs and for failures);
    ``failed`` is the set of indices that are still unusable and must be flagged.

    The reply is accepted only if it keeps the line count AND every line is a
    plausible translation of its own item. One implausible item means the reply's
    line mapping cannot be trusted — a count-preserving reply can still have one
    slot swallowing its neighbours' text and the neighbours left blank — so EVERY
    item is then re-requested on its own, not just the flagged one. Counted in
    ``counter`` either way.
    """
    results = [""] * len(texts)
    failed: set[int] = set()

    # Filter out empty line/block placeholders to preserve spacing structures
    valid = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
    if not valid:
        return results, failed
    valid_texts = [t for _, t in valid]

    try:
        translated_joined = translator.translate("\n".join(valid_texts), lang, tgt_lang)
        translated_lines = [t.strip() for t in str(translated_joined).split("\n")]

        if len(translated_lines) == len(valid_texts):
            implausible = [
                (k, reason)
                for k, (src, out) in enumerate(zip(valid_texts, translated_lines))
                if (reason := degeneration_reason(src, out))
            ]
            if not implausible:
                counter.batched += 1
                for (idx, _), res_line in zip(valid, translated_lines):
                    results[idx] = res_line
                return results, failed

            counter.fallback_implausible += 1
            logger.warning(
                "Batch reply kept its %d line(s) but %d item(s) are implausible (first: %s); its line "
                "mapping is not trusted — retrying all %d item(s) individually.",
                len(translated_lines),
                len(implausible),
                implausible[0][1],
                len(valid_texts),
            )
        else:
            # The model answered, but collapsed or added newlines, so the
            # reply cannot be mapped back onto the layout. Counted, not
            # silent: this is the common degradation and it is invisible in
            # the output — only the wall-clock changes (issue #46).
            counter.fallback_mismatch += 1
            logger.debug(
                "Batch line count %d != %d expected; retrying %d item(s) individually.",
                len(translated_lines),
                len(valid_texts),
                len(valid_texts),
            )
    except DegenerateTranslationError as exc:
        counter.fallback_implausible += 1
        logger.warning("Batch reply was degenerate (%s); retrying %d item(s) individually.", exc, len(valid_texts))
    except Exception as exc:
        # Was `except Exception: pass`. Swallowing the reason is what
        # made a 20x slowdown indistinguishable from a fast run.
        counter.fallback_error += 1
        logger.warning(
            "Batch translation call failed (%s: %s); retrying %d item(s) individually.",
            type(exc).__name__,
            exc,
            len(valid_texts),
        )

    # Safe fallback: one request per item. Items that are still unusable are
    # returned as failed — flagged by the caller, never silently accepted.
    counter.items_retried += len(valid_texts)
    for idx, text in valid:
        translated, reason = _translate_one(translator, text, lang, tgt_lang)
        if translated is None:
            failed.add(idx)
            logger.debug("Flagged for re-run (%s): %.80r", reason, text)
        else:
            results[idx] = translated.strip()
    return results, failed


# ──────────────────────────────────────────────────────────────────────────────
# Source language (only when --source_lang auto)
# ──────────────────────────────────────────────────────────────────────────────


class _SourceLanguages:
    """Per-document source-language resolution for ``--source_lang auto``.

    Resolves the language of the WHOLE document once (the context for everything
    in it), then each block / field under the same policy with its own label as a
    hint — see :mod:`processors.language` for the order and for why. With an
    explicit ``--source_lang`` it simply returns that language and detects nothing.
    """

    def __init__(self, src_lang, identifier, translator, tgt_lang, lang_policy, document_text):
        self.auto = src_lang == "auto"
        self.explicit = src_lang
        self.identifier = identifier
        self.tally = LanguageTally()
        self.policy = None
        self.document = None
        if self.auto:
            self.policy = lang_policy or SourceLanguagePolicy.from_env(
                allowed=allowed_source_languages(translator, tgt_lang)
            )
            self.document = resolve_source_language(
                identifier, document_text, self.policy, max_chars=DOCUMENT_SAMPLE_CHARS
            )

    def resolve(self, text, hint=None) -> str:
        if not self.auto:
            return self.explicit
        resolution = resolve_source_language(self.identifier, text, self.policy, hint=hint, context=self.document.lang)
        self.tally.add(resolution)
        return resolution.lang

    def translations_fields(self) -> dict:
        """Extra facts for the Document JSON ``translations`` block (auto runs only)."""
        return {"detected_source_lang": self.document.lang} if self.auto else {}

    def log(self, log_doc_id, unit) -> None:
        if not self.auto:
            return
        document = self.document
        guess = f", FastText top guess {document.raw[0]} {document.raw[1]:.2f}" if document.raw else ""
        # WARNING when FastText guesses were overridden, so an operator sees at a
        # glance how much the policy had to correct (e.g. "yue×3, krc×1").
        logger.log(
            logging.WARNING if self.tally.overridden else logging.INFO,
            "%s: source language — document %s (%s%s); %s: %s.",
            log_doc_id,
            document.lang,
            document.basis,
            guess,
            unit,
            self.tally.summary(),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Hardened parsers
# ──────────────────────────────────────────────────────────────────────────────

# For untrusted input documents: no external entities, no network DTD fetches.
_SECURE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)

# For the trusted, explicitly-supplied XSD schema document: still refuse to
# expand entities, but allow network so xs:import/xs:include can resolve.
_XSD_PARSER = etree.XMLParser(
    resolve_entities=False,
    huge_tree=False,
)


# ──────────────────────────────────────────────────────────────────────────────
# XSD validation
# ──────────────────────────────────────────────────────────────────────────────


def load_xsd(xsd_url_or_path: str) -> "etree.XMLSchema":
    """Fetch and compile an XSD schema into an ``etree.XMLSchema`` object.

    Separating network I/O from per-file validation means the schema is
    fetched exactly once per run rather than once per document (M2).
    Raises on any error so callers can abort the run cleanly.
    """
    if not xsd_url_or_path:
        raise ValueError("xsd_url_or_path must be a non-empty string.")
    if xsd_url_or_path.startswith("http"):
        req = urllib.request.Request(
            xsd_url_or_path,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # 30-second timeout to prevent infinite network hangs.
        with urllib.request.urlopen(req, timeout=30) as f:
            xmlschema_doc = etree.parse(f, parser=_XSD_PARSER)
    else:
        xmlschema_doc = etree.parse(xsd_url_or_path, parser=_XSD_PARSER)
    return etree.XMLSchema(xmlschema_doc)


def validate_xml_with_xsd(xml_tree, xmlschema: "etree.XMLSchema") -> tuple:
    """Validate *xml_tree* against a precompiled *xmlschema*.

    Accepts an ``etree.XMLSchema`` produced by :func:`load_xsd` rather than a
    URL or path, so the caller controls when and how often the schema is
    compiled.
    """
    try:
        if xmlschema.validate(xml_tree):
            return True, ""
        return False, xmlschema.error_log
    except Exception as e:
        return False, f"Validation error: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# Metadata XML processing
# ──────────────────────────────────────────────────────────────────────────────

_AMCR_NS_FALLBACK = "https://api.aiscr.cz/schema/amcr/2.2/"


def _resolve_namespaces(root) -> dict:
    xpath_ns: dict = {}

    for elem in root.iter():
        for prefix, uri in (elem.nsmap or {}).items():
            if not uri:
                continue
            if "amcr" in uri and "amcr" not in xpath_ns:
                xpath_ns["amcr"] = uri
            if "OAI-PMH" in uri and "oai" not in xpath_ns:
                xpath_ns["oai"] = uri
        if "amcr" in xpath_ns and "oai" in xpath_ns:
            break

    if "amcr" not in xpath_ns:
        xpath_ns["amcr"] = _AMCR_NS_FALLBACK
        print(f"[WARN] AMCR namespace not detected in document; falling back to '{_AMCR_NS_FALLBACK}'.")

    return xpath_ns


def _append_translation_sibling(elem, translated, src_lang, tgt_lang):
    """Insert a translated sibling straight after *elem*, keeping the source intact.

    The shape follows AMCR's own multilingual convention: the same tag, the same
    attributes, distinguished by `xml:lang`. AMCR's thesaurus records a concept as
    `<heslo xml:lang="cs">` beside `<heslo_en>` under one `@id`, and
    `load_vocab.py:124-134` reads exactly that pair on every glossary build — so
    this repo already CONSUMES append-shaped AMCR data while, in replace mode, it
    emits replace-shaped output.

    Attributes are copied rather than dropped. For the controlled-vocabulary
    elements that carry `id="HES-…"`, the id identifies the CONCEPT, not the
    string, so the English sibling belongs under the same one — dropping it would
    leave the translation unattributable. `xml:lang` is the single exception: it
    is overwritten with the target language, which is the whole point of the pair.

    Returns the new element, or ``None`` when an equivalent sibling is already
    present (see :func:`_already_appended`).
    """
    if _already_appended(elem, tgt_lang):
        return None

    sibling = etree.Element(elem.tag, nsmap=elem.nsmap)
    for key, value in elem.attrib.items():
        if key != XML_LANG:
            sibling.set(key, value)
    sibling.set(XML_LANG, tgt_lang)
    sibling.text = translated

    # Carry the source element's trailing whitespace onto the new node so the
    # inserted line inherits the document's existing indentation. `pretty_print`
    # is deliberately OFF for this writer (finding #10), so whitespace is ours to
    # get right rather than the serialiser's.
    sibling.tail = elem.tail

    # A bilingual pair is only self-describing if BOTH halves are labelled. The
    # three free-text fields this tool targets (nazev/popis/poznamka) carry no
    # `xml:lang` in any of the 15 shipped AMCR samples, so without this the output
    # would assert English on one element and nothing on its Czech twin.
    if not elem.get(XML_LANG) and src_lang and src_lang != "auto":
        elem.set(XML_LANG, src_lang)

    elem.addnext(sibling)
    return sibling


def _already_appended(elem, tgt_lang) -> bool:
    """True when *elem* is itself a translation, or already has one beside it.

    This is the idempotency guard the tool has never had. In replace mode,
    re-running over an `_en` output silently re-translates English into English —
    with an explicit `--source_lang` there is not even language detection to
    notice. Append mode can do better because its output is self-describing: the
    `xml:lang` marker that makes the pair readable also makes a second pass a
    no-op instead of a duplicate.
    """
    if elem.get(XML_LANG) == tgt_lang:
        return True
    nxt = elem.getnext()
    return nxt is not None and nxt.tag == elem.tag and nxt.get(XML_LANG) == tgt_lang


def _metadata_record_text(root, xpaths, xpath_ns) -> str:
    """All targeted field texts of a record, joined — the sample its language is resolved from."""
    texts = []
    for xpath in xpaths:
        try:
            found = root.xpath(xpath, namespaces=xpath_ns)
        except etree.XPathError:
            continue
        for elem in found if isinstance(found, list) else []:
            text = getattr(elem, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return " ".join(texts)[:DOCUMENT_SAMPLE_CHARS]


def _write_metadata_translation(elem, translated, src_lang, tgt_lang, output_mode) -> int:
    """Apply one field's translation in the chosen mode; returns 1 if a sibling was appended."""
    if output_mode == OUTPUT_MODE_APPEND:
        return 1 if _append_translation_sibling(elem, translated, src_lang, tgt_lang) is not None else 0
    elem.text = translated
    return 0


def _rerun_flagged_metadata(flagged, translator, tgt_lang, output_mode, log_doc_id) -> int:
    """End-of-document re-run of metadata fields flagged as degenerate.

    Same policy as the ALTO path (:func:`_rerun_flagged_alto`): after the whole
    record has been processed, wait ``TRANSLATION_RERUN_DELAY_S`` and re-request
    each flagged field on its own, for ``TRANSLATION_RERUN_ROUNDS`` rounds. A
    recovered field is written as usual and logged ``rerun``; a field that never
    recovers keeps its source text — replace mode leaves it as it was, append mode
    adds no sibling — and is logged ``untranslated`` with an empty target.
    Returns how many siblings were appended.
    """
    rounds, delay_s = _rerun_policy()
    logger.warning(
        "%s: %d field(s) were flagged during processing; re-running them (%d round(s), %.1f s cool-down).",
        log_doc_id,
        len(flagged),
        rounds,
        delay_s,
    )
    appended = 0
    recovered = 0
    for _round in range(rounds):
        todo = [item for item in flagged if not item.get("done")]
        if not todo:
            break
        if delay_s > 0:
            time.sleep(delay_s)
        for item in todo:
            translated, reason = _translate_one(translator, item["text"], item["lang"], tgt_lang)
            if translated is None:
                item["reason"] = reason
                continue
            appended += _write_metadata_translation(item["elem"], translated, item["lang"], tgt_lang, output_mode)
            item["row"][4] = translated
            item["row"][5] = STATUS_RERUN
            item["done"] = True
            recovered += 1

    untranslated = 0
    for item in flagged:
        if item.get("done"):
            continue
        untranslated += 1
        item["row"][5] = STATUS_UNTRANSLATED
        logger.warning(
            "%s: %s is still degenerate after the re-run (%s); its source text is kept: %.80r",
            log_doc_id,
            item["row"][2],
            item["reason"],
            item["text"],
        )
    logger.warning(
        "%s: %d flagged field(s) recovered on the re-run, %d left untranslated (source text kept).",
        log_doc_id,
        recovered,
        untranslated,
    )
    return appended


def process_metadata_xml(
    input_path,
    output_path,
    xpaths,
    translator,
    src_lang,
    tgt_lang,
    xsd_schema=None,
    csv_writer=None,
    identifier=None,
    doc=None,
    backend=None,
    doc_id=None,
    output_mode=DEFAULT_OUTPUT_MODE,
    lang_policy=None,
):
    output_mode = normalize_output_mode(output_mode)
    try:
        tree = etree.parse(str(input_path), parser=_SECURE_PARSER)
        root = tree.getroot()
        xpath_ns = _resolve_namespaces(root)

        # With --source_lang auto the record's own language is resolved first, from
        # all of its targeted fields together: a field too short or too ambiguous to
        # judge on its own inherits it (see processors/language.py).
        record_text = _metadata_record_text(root, xpaths, xpath_ns) if src_lang == "auto" else ""
        languages = _SourceLanguages(src_lang, identifier, translator, tgt_lang, lang_policy, record_text)

        # D3 (atrium-project#10): the CSV log's `file` column carries the SAME doc_id the
        # caller keyed the document record on, passed in rather than re-derived per row.
        # main.py already computed it via canonical_doc_id(); the old inline
        # `input_path.name.split(".")[0]` was a third independent derivation of the same
        # value, which is how a multi-dot filename ends up logged under one id and recorded
        # under another. `doc_id=None` (a direct caller, e.g. a unit test) still derives it —
        # through the shared function, never by hand.
        log_doc_id = doc_id or canonical_doc_id(input_path)

        appended = 0
        skipped_existing = 0
        # The QA log is buffered and written once at the end, in document order,
        # because a flagged field only gets its final value after the re-run.
        rows = []
        flagged = []

        for xpath in xpaths:
            try:
                elements = root.xpath(xpath, namespaces=xpath_ns)
                for elem in elements:
                    original_text = elem.text
                    if not original_text or not original_text.strip():
                        continue

                    # Issue #46, the big question, decided here and nowhere else.
                    # REPLACE overwrites the Czech text node; APPEND leaves it and
                    # inserts a `xml:lang`-marked sibling beside it. Everything
                    # upstream of this point is identical in both modes.
                    if output_mode == OUTPUT_MODE_APPEND and _already_appended(elem, tgt_lang):
                        # Second pass over an already-appended document: skip the
                        # API call entirely rather than translate and discard.
                        skipped_existing += 1
                        continue

                    # The field's own `xml:lang` (AMCR labels many) is the hint.
                    actual_src_lang = languages.resolve(original_text, hint=elem.get(XML_LANG))

                    row = [log_doc_id, "", xpath, original_text, "", STATUS_OK]
                    rows.append(row)

                    translated, reason = _translate_one(translator, original_text, actual_src_lang, tgt_lang)
                    if translated is None:
                        # Degenerate even after the backend's own retries: leave the
                        # field exactly as it is for now and re-run it at the end.
                        flagged.append(
                            {"elem": elem, "text": original_text, "lang": actual_src_lang, "row": row, "reason": reason}
                        )
                        continue

                    appended += _write_metadata_translation(elem, translated, actual_src_lang, tgt_lang, output_mode)
                    row[4] = translated

            except etree.XPathError as e:
                print(f"[WARN] XPath error for '{xpath}': {e}")

        if flagged:
            appended += _rerun_flagged_metadata(flagged, translator, tgt_lang, output_mode, log_doc_id)

        if csv_writer:
            for row in rows:
                csv_writer.writerow(row)

        languages.log(log_doc_id, "fields")

        # ATRIUM Document JSON accretion update for metadata blocks.
        #
        # `translations` is metadata about the language pair/backend, per the
        # schema (`{source_lang, target_lang, backend}`) — NOT the translated
        # corpus text itself, which already persists via `derived_from.translated_xml`.
        #
        # Entity translation (`entities[].translation_en`) is NOT attempted here, and as of
        # today no code path in this repo writes that field at all — it is UNIMPLEMENTED,
        # not merely deferred (atrium-project#10, finding D7).
        #
        # Why it is absent here: in the declared pipeline order (pc→alto→translate→nlp→llm),
        # `entities[]` is produced by nlp-enrich, which runs AFTER the translator. `entities`
        # does not exist yet at this point in any real run, so a per-entity translation pass
        # in this function would be unreachable dead code (issue #13 alignment audit, P0.6).
        #
        # This repo is nonetheless the field's declared OWNER — see the ownership table in the
        # hub's `docs/document_schema.md` and `BLOCK_FIELD_OWNERS["entities"]["translator"]` in
        # atrium_document.py — so the gap is ours to close, not another tool's. Resolving it
        # needs a SECOND pass over an already-enriched record: read `entities` via
        # `doc.get_block("entities")`, translate each `surface`, and `merge_block("entities",
        # rows, own_fields=["translation_en"])` back (followed by
        # `assert_fields_survived("entities", rows, ["translation_en"])`, so a grant mistake
        # cannot silently drop the field). That is a feature, tracked as still-open; the
        # earlier citation here pointed at `agent_dev_logs/digests/13.digest.md`, which does
        # not exist in this repo.
        if output_mode == OUTPUT_MODE_APPEND:
            logger.info(
                "%s: append mode wrote %d translated sibling(s); %d field(s) already carried one.",
                log_doc_id,
                appended,
                skipped_existing,
            )

        if doc is not None:
            doc.set_block(
                "translations",
                {
                    "source_lang": src_lang,
                    "target_lang": tgt_lang,
                    "backend": backend or "lindat",
                    # #46: which shape this artifact actually has. `additionalProperties`
                    # is true on this block, so recording it needs no schema change —
                    # and a consumer that finds English in a field needs to know whether
                    # the Czech was kept beside it or overwritten.
                    "output_mode": output_mode,
                    # With --source_lang auto, "auto" says nothing about the record;
                    # the language it was resolved to does.
                    **languages.translations_fields(),
                },
            )

        if xsd_schema:
            print(f"[INFO] Validating {output_path.name} against XSD …")
            is_valid, error_log = validate_xml_with_xsd(tree, xsd_schema)
            if is_valid:
                print(f"[SUCCESS] XSD validation passed for {output_path.name}")
            else:
                print(f"[WARN] XSD validation failed:\n{error_log}")

        # pretty_print is intentionally OFF: it reflows whitespace and can perturb
        # significant whitespace in mixed-content elements (finding #10). Leaving
        # it off keeps the output diff minimal against the source tree.
        tree.write(
            str(output_path),
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=False,
        )
        print(f"[SUCCESS] Saved metadata translation → {output_path}")

    except Exception as e:
        print(f"[ERROR] Failed to process metadata XML '{input_path}': {e}")
        raise


# ──────────────────────────────────────────────────────────────────────────────
# ALTO XML processing Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _align_tokens_to_lines(block_text, line_translations, source_line_texts=None):
    """
    Partitions the high-quality block translation into buckets corresponding
    to physical XML lines, using the lower-quality line translations as anchors.

    Called WITHOUT *source_line_texts* this is the original anchor-trusting
    aligner, kept byte-for-byte for existing callers: an empty anchor gets an
    empty bucket, and the final line absorbs the remainder.

    Called WITH the source line texts (what ``process_alto_xml`` does) it cannot be
    steered by a bad anchor — see :func:`_align_block`.
    """
    if source_line_texts is not None:
        return _align_block(block_text, line_translations, source_line_texts)[0]

    block_tokens = block_text.split() if block_text else []
    if not block_tokens:
        return [[] for _ in line_translations]
    if len(line_translations) <= 1:
        return [block_tokens]

    assigned_buckets = []
    remaining_tokens = block_tokens

    for line_tgt in line_translations[:-1]:
        line_tokens = line_tgt.split() if line_tgt else []
        expected_len = len(line_tokens)

        # If the original line had no text, assign 0 tokens
        if expected_len == 0 or not remaining_tokens:
            assigned_buckets.append([])
            continue

        # Define a sliding window search range (+/- 50% of expected words)
        min_idx = max(0, int(expected_len * 0.5) - 1)
        max_idx = min(len(remaining_tokens), int(expected_len * 1.5) + 2)

        best_idx = 0
        best_ratio = -1.0

        # Find the split point that maximizes similarity to the line translation anchor
        for split_idx in range(min_idx, max_idx + 1):
            candidate_str = " ".join(remaining_tokens[:split_idx])
            ratio = difflib.SequenceMatcher(None, candidate_str, line_tgt).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = split_idx

        assigned_buckets.append(remaining_tokens[:best_idx])
        remaining_tokens = remaining_tokens[best_idx:]

    # The final line gets whatever tokens are left over
    assigned_buckets.append(remaining_tokens)
    return assigned_buckets


def _best_anchor_split(remaining, anchor, lo, hi):
    """Split index in ``[lo, hi]`` whose prefix of *remaining* best matches *anchor*.

    The ±50 % window around the anchor's word count and the ``difflib`` ratio are
    the original aligner's; *lo* / *hi* are the guard rails the caller adds.
    """
    expected_len = len(anchor.split())
    min_idx = max(lo, int(expected_len * 0.5) - 1)
    max_idx = min(hi, int(expected_len * 1.5) + 2)
    if min_idx > max_idx:
        min_idx = max_idx
    best_idx, best_ratio = min_idx, -1.0
    for split_idx in range(min_idx, max_idx + 1):
        ratio = difflib.SequenceMatcher(None, " ".join(remaining[:split_idx]), anchor).ratio()
        if ratio > best_ratio:
            best_ratio, best_idx = ratio, split_idx
    return best_idx


def _align_block(block_text, line_translations, source_line_texts):
    """Source-aware line alignment. Returns ``(buckets, approximated_line_indices)``.

    This is what went wrong on the 2026-09-26 samples: the Pass-2 line anchors
    are one batched request per page, a degenerate or shifted reply fills some
    anchor slots with 50+ words and leaves others empty, and the anchor-trusting
    aligner turned that into lines holding 71 words next to lines holding none.
    Anchors are never written anywhere, so none of it was visible in the CSV.

    Here every anchor is checked against its OWN source line before it is used:

    * a line with no source text gets an empty bucket (unchanged invariant);
    * a *usable* anchor (non-empty, and not degenerate relative to its source
      line per :func:`processors.quality.degeneration_reason`) drives the
      original ``difflib`` window search;
    * an unusable anchor is replaced by a proportional share of the REMAINING
      tokens by the REMAINING source word counts — so an error cannot accumulate
      down the block — and the line is reported as approximated;
    * no line with source text is starved while tokens remain: each takes at
      least one, and none may take the tokens every later text line needs;
    * the last line WITH source text receives the remainder, so every token is
      kept, in order, on a line that has ``String`` elements to hold it.
    """
    source_line_texts = list(source_line_texts)
    n = len(source_line_texts)
    block_tokens = block_text.split() if block_text else []
    if not block_tokens:
        return [[] for _ in range(n)], set()
    if n <= 1:
        return [block_tokens], set()

    src_counts = [len(text.split()) if text else 0 for text in source_line_texts]
    anchors = list(line_translations or []) + [""] * n
    text_lines = [i for i, count in enumerate(src_counts) if count]
    if not text_lines:
        return [[] for _ in range(n - 1)] + [block_tokens], set()
    last_text_line = text_lines[-1]

    def _usable(i):
        anchor = anchors[i] or ""
        return bool(anchor.strip()) and degeneration_reason(source_line_texts[i], anchor) is None

    approximated = {i for i in text_lines if not _usable(i)} if len(text_lines) > 1 else set()

    buckets = [[] for _ in range(n)]
    remaining = block_tokens
    for i in text_lines[:-1]:
        if not remaining:
            break
        later_text_lines = sum(1 for j in text_lines if j > i)
        hi = max(1, len(remaining) - later_text_lines)
        if i in approximated:
            share = len(remaining) * src_counts[i] / sum(src_counts[j] for j in text_lines if j >= i)
            take = min(hi, max(1, round(share)))
        else:
            take = _best_anchor_split(remaining, anchors[i], 1, hi)
        buckets[i] = remaining[:take]
        remaining = remaining[take:]
    buckets[last_text_line] = remaining
    return buckets, approximated


def _align_tokens_proportional(block_text, source_line_texts):
    """
    Anchor-free alternative to :func:`_align_tokens_to_lines` (review finding #4).

    Distributes the Pass-1 block tokens across physical lines in proportion to
    each line's *source* word count, so no per-line translation API call is
    needed. Honours the same invariants the reconstruction relies on:
      * token conservation (no token lost, reordered, or duplicated);
      * one bucket per line;
      * empty source line → empty bucket;
      * the final line absorbs the remainder.

    Used only when ``process_alto_xml(..., line_anchors=False)`` (the ``--fast-align``
    CLI flag). The default path uses the anchor-guided :func:`_align_block`, which
    falls back to this same proportional rule line by line whenever an anchor
    cannot be trusted.
    """
    block_tokens = block_text.split() if block_text else []
    if not block_tokens:
        return [[] for _ in source_line_texts]
    if len(source_line_texts) <= 1:
        return [block_tokens]

    counts = [len(t.split()) if t else 0 for t in source_line_texts]
    total = sum(counts)

    # No source words anywhere: dump everything into the last line.
    if total == 0:
        return [[] for _ in source_line_texts[:-1]] + [block_tokens]

    buckets = []
    remaining = block_tokens
    for cnt in counts[:-1]:
        if cnt == 0 or not remaining:
            buckets.append([])
            continue
        take = round(len(block_tokens) * cnt / total)
        take = max(0, min(take, len(remaining)))
        buckets.append(remaining[:take])
        remaining = remaining[take:]
    buckets.append(remaining)  # final line takes the remainder
    return buckets


def _distribute_tokens(num_strings, tokens):
    """Spread one line's bucket over its ``String`` elements — the greedy 1:1 rule.

    Each ``String`` but the last takes one token (``""`` once the bucket runs
    out); the last takes whatever is left, so no token is dropped. Returns one
    value per ``String``. The same values go into ``CONTENT`` (replace) or into a
    translation ``ALTERNATIVE`` (append), so both modes place words identically.
    """
    values = []
    for i in range(num_strings):
        if i < num_strings - 1:
            values.append(tokens[i] if i < len(tokens) else "")
        else:
            values.append(" ".join(tokens[i:]))
    return values


# ──────────────────────────────────────────────────────────────────────────────
# ALTO XML processing
# ──────────────────────────────────────────────────────────────────────────────

#: ``PURPOSE`` of the ``<ALTERNATIVE>`` that carries a ``String``'s translation in
#: append mode. ALTO (2.1+) defines ``ALTERNATIVE`` as "any alternative for the
#: word" with a free-text ``PURPOSE``; there is no language attribute on it, so
#: the target language travels in the purpose string.
ALTO_TRANSLATION_PURPOSE = "translation:{lang}"

# Attributes ALTO uses for a language label: ``LANG`` (TextBlock/TextLine/String
# in ALTO 2+), and the older ``language`` on TextBlock.
_ALTO_LANG_ATTRS = ("LANG", "language")


def _alto_localname(elem):
    # `.iter()` also yields comments/PIs, whose `.tag` is a callable that
    # etree.QName rejects — real scanner ALTO routinely carries comments.
    return etree.QName(elem).localname if isinstance(elem.tag, str) else None


def _translation_purpose(tgt_lang):
    return ALTO_TRANSLATION_PURPOSE.format(lang=tgt_lang)


def _alto_language_label(block):
    """The block's existing language label (``LANG``, or ALTO 3's ``language``), if any."""
    for attr in _ALTO_LANG_ATTRS:
        value = block.get(attr)
        if value:
            return value
    return None


def _alto_document_text(root, limit=DOCUMENT_SAMPLE_CHARS) -> str:
    """The document's ``String/@CONTENT`` words in reading order, up to *limit* characters."""
    words, size = [], 0
    for elem in root.iter():
        if _alto_localname(elem) != "String":
            continue
        content = elem.get("CONTENT")
        if content:
            words.append(content)
            size += len(content) + 1
            if size >= limit:
                break
    return " ".join(words)


def _block_has_translation(block, tgt_lang) -> bool:
    """True when an append-mode pass already put a *tgt_lang* translation in *block*."""
    purpose = _translation_purpose(tgt_lang)
    return any(_alto_localname(elem) == "ALTERNATIVE" and elem.get("PURPOSE") == purpose for elem in block.iter())


def _set_translation_alternative(string_elem, value, tgt_lang) -> bool:
    """Append mode: put *value* in the String's translation ``ALTERNATIVE``.

    ``CONTENT`` is never touched. At most one translation alternative per target
    language exists afterwards (a re-run replaces it rather than stacking a
    second). Schema order inside ``String`` is ``Shape?, ALTERNATIVE*, Glyph*``, so
    the new element goes after any ``Shape`` / existing ``ALTERNATIVE`` and before
    any ``Glyph``. An empty *value* writes nothing. Returns whether one was written.
    """
    purpose = _translation_purpose(tgt_lang)
    existing = [
        child for child in string_elem if _alto_localname(child) == "ALTERNATIVE" and child.get("PURPOSE") == purpose
    ]
    for extra in existing[1:]:
        string_elem.remove(extra)
    if not value:
        if existing:
            string_elem.remove(existing[0])
        return False
    if existing:
        existing[0].text = value
        return True

    namespace = etree.QName(string_elem).namespace
    tag = f"{{{namespace}}}ALTERNATIVE" if namespace else "ALTERNATIVE"
    # SubElement inherits the parent's namespace map, so the element serialises
    # in the document's default namespace instead of growing an `ns0:` prefix.
    alternative = etree.SubElement(string_elem, tag)
    alternative.set("PURPOSE", purpose)
    alternative.text = value
    position = 0
    for index, child in enumerate(string_elem):
        if child is alternative:
            break
        if _alto_localname(child) in ("Shape", "ALTERNATIVE"):
            position = index + 1
    string_elem.insert(position, alternative)
    return True


def _relabel_translated_block(block, tgt_lang) -> int:
    """Replace mode: move EXISTING language labels in *block* to *tgt_lang*.

    Scanner ALTO arrives labelled — the shipped ABBYY sample carries
    ``TextBlock LANG="cs"`` (and ``"sk"``) on every block — and replace mode used to
    leave that label on English text, so the output asserted Czech about English.
    Only labels that already exist are changed; none is added, so an unlabelled
    input stays unlabelled exactly as before.
    """
    if not tgt_lang:
        return 0
    touched = 0
    for elem in block.iter():
        if not isinstance(elem.tag, str):
            continue
        for attr in _ALTO_LANG_ATTRS:
            current = elem.get(attr)
            if current is not None and current != tgt_lang:
                elem.set(attr, tgt_lang)
                touched += 1
    return touched


def _label_source_block(block, src_lang) -> bool:
    """Append mode: make sure the block states the language its ``CONTENT`` is in.

    The ``CONTENT`` stays in the source language, so that is the block's language.
    Labels already present are left alone; a missing one is added when the source
    language is actually known (not ``auto``). The translation half is labelled by
    its ``ALTERNATIVE``'s ``PURPOSE``, so both halves are self-describing — the
    ALTO counterpart of the metadata pair ``xml:lang="cs"`` / ``xml:lang="en"``.
    """
    if not src_lang or src_lang == "auto":
        return False
    if any(block.get(attr) is not None for attr in _ALTO_LANG_ATTRS):
        return False
    block.set("LANG", src_lang)
    return True


def _request_block_anchors(bdata, translator, tgt_lang, counter):
    """Pass-2 anchors for ONE block (used when a block is recovered by the re-run)."""
    lines_data = bdata["lines_data"]
    if sum(1 for ld in lines_data if ld["orig_text"]) < 2:
        return
    results, failed = _translate_items(
        translator, [ld["orig_text"] for ld in lines_data], bdata["actual_src_lang"], tgt_lang, counter
    )
    for k, (ld, tgt) in enumerate(zip(lines_data, results)):
        ld["line_tgt"] = tgt
        ld["anchor_failed"] = k in failed


def _rerun_flagged_alto(pending, translator, tgt_lang, line_anchors, counter, log_doc_id):
    """End-of-document re-run of every block and line anchor flagged during processing.

    The failure it exists for is transient — the same request that looped on
    ``"pravidla"`` succeeds minutes later — so the flagged segments are retried
    after the whole document has been processed, after a cool-down, one segment
    per request (``TRANSLATION_RERUN_ROUNDS`` rounds, ``TRANSLATION_RERUN_DELAY_S``
    before each). A recovered block also gets its line anchors. Whatever is still
    failing afterwards is left for the caller to keep as source.
    """
    rounds, delay_s = _rerun_policy()
    flagged_blocks = [b for b in pending if b["block_failed"]]
    flagged_anchors = [ld for b in pending if not b["block_failed"] for ld in b["lines_data"] if ld["anchor_failed"]]
    counter.segments_flagged += len(flagged_blocks)
    counter.anchors_flagged += len(flagged_anchors)
    logger.warning(
        "%s: %d block(s) and %d line anchor(s) were flagged during processing; re-running them "
        "(%d round(s), %.1f s cool-down).",
        log_doc_id,
        len(flagged_blocks),
        len(flagged_anchors),
        rounds,
        delay_s,
    )

    for _round in range(rounds):
        todo_blocks = [b for b in pending if b["block_failed"]]
        todo_anchors = [
            (b, ld) for b in pending if not b["block_failed"] for ld in b["lines_data"] if ld["anchor_failed"]
        ]
        if not todo_blocks and not todo_anchors:
            break
        if delay_s > 0:
            time.sleep(delay_s)

        for bdata in todo_blocks:
            translated, _reason = _translate_one(translator, bdata["block_text"], bdata["actual_src_lang"], tgt_lang)
            if translated is None:
                continue
            bdata["block_tgt"] = translated.strip()
            bdata["block_failed"] = False
            bdata["block_rerun"] = True
            counter.segments_recovered += 1
            if line_anchors:
                _request_block_anchors(bdata, translator, tgt_lang, counter)

        for bdata, ld in todo_anchors:
            translated, _reason = _translate_one(translator, ld["orig_text"], bdata["actual_src_lang"], tgt_lang)
            if translated is None:
                continue
            ld["line_tgt"] = translated.strip()
            ld["anchor_failed"] = False
            ld["anchor_rerun"] = True
            counter.anchors_recovered += 1

    for bdata in pending:
        if bdata["block_failed"]:
            counter.segments_untranslated += 1
            logger.warning(
                "%s: page %s block %s is still degenerate after the re-run; its source text is kept "
                "and its lines are flagged 'untranslated' in the CSV log: %.80r",
                log_doc_id,
                bdata["page_idx"],
                bdata["block_idx"],
                bdata["block_text"],
            )


def _finalize_alto_block(bdata, output_mode, tgt_lang, line_anchors, counter) -> int:
    """Write one block's translation into its Strings; set each line's CSV text/status.

    Returns how many translation ``ALTERNATIVE`` elements were written (append).
    """
    lines_data = bdata["lines_data"]
    if bdata["block_failed"]:
        # Never recovered: the Strings keep their source text and geometry, and the
        # CSV says so instead of carrying garbage or a silent blank.
        for ld in lines_data:
            ld["trans_line_text"] = ""
            ld["status"] = STATUS_UNTRANSLATED if ld["orig_text"] else STATUS_OK
        return 0

    source_texts = [ld["orig_text"] for ld in lines_data]
    if line_anchors:
        buckets, approximated = _align_block(
            bdata["block_tgt"], [ld.get("line_tgt", "") for ld in lines_data], source_texts
        )
    else:
        buckets, approximated = _align_tokens_proportional(bdata["block_tgt"], source_texts), set()
    counter.anchors_approximated += len(approximated)

    written = 0
    for index, (ld, tokens) in enumerate(zip(lines_data, buckets)):
        strings = ld["strings"]
        if strings:
            for string_elem, value in zip(strings, _distribute_tokens(len(strings), tokens)):
                if output_mode == OUTPUT_MODE_APPEND:
                    written += _set_translation_alternative(string_elem, value, tgt_lang)
                else:
                    string_elem.set("CONTENT", value)
            ld["trans_line_text"] = " ".join(tokens)

        if not ld["orig_text"]:
            ld["status"] = STATUS_OK
        elif index in approximated:
            ld["status"] = STATUS_APPROX
        elif bdata["block_rerun"] or ld.get("anchor_rerun"):
            ld["status"] = STATUS_RERUN
        else:
            ld["status"] = STATUS_OK

    if output_mode == OUTPUT_MODE_APPEND:
        _label_source_block(bdata["block"], bdata["actual_src_lang"])
    else:
        _relabel_translated_block(bdata["block"], tgt_lang)
    return written


def process_alto_xml(
    input_path,
    output_path,
    translator,
    src_lang,
    tgt_lang,
    csv_writer=None,
    identifier=None,
    line_anchors=True,
    doc=None,
    backend=None,
    doc_id=None,
    output_mode=DEFAULT_OUTPUT_MODE,
    lang_policy=None,
):
    """
    Translate an ALTO XML document (dual-pass reconstruction).

    Implements Page-Level Batching (Issue #16): Pools block and line translation
    requests per page to eliminate heavy API call overhead, falling back to
    1-by-1 processing if the NMT model modifies layout boundaries — or, since the
    2026-09-26 regression, if any item of a batched reply is implausible.

    *doc_id* is the caller's canonical doc_id for this document, used verbatim as the CSV
    log's ``file`` column; see the metadata-path twin for why it is passed in (D3).

    VALIDATION, FLAGGING AND RE-RUN (issue #46 follow-up). No reply is trusted on
    its line count alone. Every batched item — block translations and line anchors
    alike — is checked against its own source with
    :func:`processors.quality.degeneration_reason`; one implausible item means the
    batch's line mapping is not trusted and every item is re-requested on its own.
    A block or anchor that is STILL unusable is flagged, and all flagged segments
    are re-run once the whole document has been processed (see
    :func:`_rerun_flagged_alto`). A block that never recovers keeps its source text
    — it is never blanked and never filled with garbage. Line anchors are judged
    against their source lines before they may steer the alignment
    (:func:`_align_block`). The CSV log is written once, in document order, with a
    ``status`` per line: ``ok``, ``rerun``, ``approx_alignment`` or ``untranslated``.

    OUTPUT MODE (issue #46).

    * ``replace`` writes the aligned tokens into ``String/@CONTENT``, and moves any
      language label the block already carries (ABBYY writes ``LANG="cs"``) to the
      target language.
    * ``append`` keeps ``CONTENT`` — the source text and its geometry stay exactly
      as scanned — and adds the same aligned tokens as
      ``<ALTERNATIVE PURPOSE="translation:<tgt>">`` inside each ``String``: ALTO's own
      element for "an alternative for the word". The block keeps (or gains) its
      source-language label. The ``String`` inventory is 1:1 with the source in both
      modes, and a second append pass skips blocks that already carry a translation.

    The per-``String`` split is still a distribution of ONE block translation over
    the scanned boxes, not a word-by-word translation — the ``ALTERNATIVE`` says what
    English landed on that box, which is exactly what replace mode writes into it.
    """
    output_mode = normalize_output_mode(output_mode)
    counter = BatchFallbackCounter()
    try:
        # D3: one doc_id per document, supplied by the caller (main.py) or derived through
        # the shared canonical_doc_id() — never hand-rolled here. See process_metadata_xml.
        log_doc_id = doc_id or canonical_doc_id(input_path)

        tree = etree.parse(str(input_path), parser=_SECURE_PARSER)
        root = tree.getroot()

        nsmap = root.nsmap
        ns = {"alto": nsmap[None]} if None in nsmap else nsmap
        use_ns = "alto" in ns

        pages = root.xpath("//alto:Page", namespaces=ns) if use_ns else root.xpath("//Page")
        total_pages = len(pages)

        # With --source_lang auto the document's language is resolved once, up front,
        # from its text; a block too short or too ambiguous to judge on its own — or
        # one FastText assigns a language the backend cannot translate — inherits it
        # (after its own LANG label). See processors/language.py.
        languages = _SourceLanguages(
            src_lang,
            identifier,
            translator,
            tgt_lang,
            lang_policy,
            _alto_document_text(root) if src_lang == "auto" else "",
        )

        # Every translatable block in document order (the CSV is written from this),
        # and the ones flagged for the end-of-document re-run.
        document_blocks = []
        pending = []
        skipped_existing = 0
        alternatives_written = 0

        for page_idx, page in enumerate(pages, 1):
            text_blocks = page.xpath(".//alto:TextBlock", namespaces=ns) if use_ns else page.xpath(".//TextBlock")
            page_lines = page.xpath(".//alto:TextLine", namespaces=ns) if use_ns else page.xpath(".//TextLine")

            num_blocks = len(text_blocks)
            num_lines = len(page_lines)

            print(f"[INFO] Page {page_idx}/{total_pages} - Found {num_blocks} text blocks and {num_lines} text lines.")

            # ──────────────────────────────────────────────────────────────────
            # PHASE 1: Gather original line structures and block text
            # ──────────────────────────────────────────────────────────────────
            page_blocks_data = []
            for block_idx, block in enumerate(text_blocks, 1):
                # Append is idempotent: a block that already carries a translation
                # ALTERNATIVE for this target is not sent to the backend again.
                if output_mode == OUTPUT_MODE_APPEND and _block_has_translation(block, tgt_lang):
                    skipped_existing += 1
                    continue

                lines = block.xpath(".//alto:TextLine", namespaces=ns) if use_ns else block.xpath(".//TextLine")

                all_strings = []
                lines_data = []

                for line_idx, line in enumerate(lines, 1):
                    line_id = line.get("ID", str(line_idx))
                    strings = line.xpath(".//alto:String", namespaces=ns) if use_ns else line.xpath(".//String")

                    orig_line_text = " ".join(s.get("CONTENT", "") for s in strings if s.get("CONTENT")).strip()
                    lines_data.append(
                        {
                            "id": line_id,
                            "strings": strings,
                            "orig_text": orig_line_text,
                            "trans_line_text": "",
                            "line_tgt": "",
                            "anchor_failed": False,
                            "anchor_rerun": False,
                            "status": STATUS_OK,
                        }
                    )
                    all_strings.extend(strings)

                block_text = " ".join(ld["orig_text"] for ld in lines_data if ld["orig_text"]).strip()
                if not block_text or not all_strings:
                    continue

                # The block's own LANG (ABBYY writes one per block) is the hint.
                actual_src_lang = languages.resolve(block_text, hint=_alto_language_label(block))

                page_blocks_data.append(
                    {
                        "page_idx": page_idx,
                        "block_idx": block_idx,
                        "block": block,
                        "lines_data": lines_data,
                        "block_text": block_text,
                        "actual_src_lang": actual_src_lang,
                        "block_tgt": "",
                        "block_failed": False,
                        "block_rerun": False,
                    }
                )

            if not page_blocks_data:
                continue
            document_blocks.extend(page_blocks_data)

            # ──────────────────────────────────────────────────────────────────
            # PHASE 2: Page-Level Batch Translation (Grouped by Language)
            # ──────────────────────────────────────────────────────────────────
            lang_groups = {}
            for bdata in page_blocks_data:
                lang_groups.setdefault(bdata["actual_src_lang"], []).append(bdata)

            for lang, group in lang_groups.items():
                # Pass 1: Batch translate full blocks
                translated_blocks, failed_blocks = _translate_items(
                    translator, [b["block_text"] for b in group], lang, tgt_lang, counter
                )
                for k, (bdata, tgt) in enumerate(zip(group, translated_blocks)):
                    bdata["block_tgt"] = tgt
                    bdata["block_failed"] = k in failed_blocks

                # Pass 2: Batch translate lines as structural anchors — only where an
                # anchor can matter: a translated block with at least two text lines.
                # (A single-line block takes the whole block translation as it is.)
                if line_anchors:
                    line_texts = []
                    line_refs = []
                    for bdata in group:
                        if bdata["block_failed"] or sum(1 for ld in bdata["lines_data"] if ld["orig_text"]) < 2:
                            continue
                        for ld in bdata["lines_data"]:
                            line_texts.append(ld["orig_text"])
                            line_refs.append(ld)

                    translated_lines, failed_lines = _translate_items(translator, line_texts, lang, tgt_lang, counter)
                    for k, (ld, tgt) in enumerate(zip(line_refs, translated_lines)):
                        ld["line_tgt"] = tgt
                        ld["anchor_failed"] = k in failed_lines

            # ──────────────────────────────────────────────────────────────────
            # PHASE 3: Redistribution — flagged blocks wait for the re-run
            # ──────────────────────────────────────────────────────────────────
            for bdata in page_blocks_data:
                sys.stdout.write(
                    f"\r[INFO] Page {page_idx}/{total_pages} | Processing block {bdata['block_idx']}/{num_blocks}"
                )
                sys.stdout.flush()

                if bdata["block_failed"] or any(ld["anchor_failed"] for ld in bdata["lines_data"]):
                    pending.append(bdata)
                    continue
                alternatives_written += _finalize_alto_block(bdata, output_mode, tgt_lang, line_anchors, counter)

            if num_blocks > 0:
                print()

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 4: End-of-document re-run of everything flagged above
        # ──────────────────────────────────────────────────────────────────────
        if pending:
            _rerun_flagged_alto(pending, translator, tgt_lang, line_anchors, counter, log_doc_id)
            for bdata in pending:
                alternatives_written += _finalize_alto_block(bdata, output_mode, tgt_lang, line_anchors, counter)

        # The QA log, once, in document order — flagged blocks were finalised late,
        # so writing per page would have put their rows in the wrong place.
        if csv_writer:
            for bdata in document_blocks:
                for ld in bdata["lines_data"]:
                    if ld["orig_text"] or ld["trans_line_text"]:
                        csv_writer.writerow(
                            [
                                log_doc_id,
                                bdata["page_idx"],
                                ld["id"],
                                ld["orig_text"],
                                ld["trans_line_text"],
                                ld["status"],
                            ]
                        )

        # Throughput and quality reality, reported once per document. A run that
        # batched cleanly and one that fell back or re-ran on every page differ by an
        # order of magnitude in backend calls — this line is where that shows (#46).
        if counter.needs_attention:
            logger.warning("%s: %s.", log_doc_id, counter.summary())
        else:
            logger.info("%s: %s.", log_doc_id, counter.summary())

        languages.log(log_doc_id, "blocks")

        if output_mode == OUTPUT_MODE_APPEND:
            logger.info(
                "%s: append mode kept the source CONTENT and wrote %d translation ALTERNATIVE(s) "
                "(PURPOSE=%r); %d block(s) already carried one.",
                log_doc_id,
                alternatives_written,
                _translation_purpose(tgt_lang),
                skipped_existing,
            )

        # ATRIUM Document JSON accretion update for ALTO blocks. See the metadata-path
        # twin above for why `translations` carries language-pair metadata (not the
        # translated corpus text), and for the state of `entities[].translation_en` — a
        # field this repo owns and no code path here writes (D7, still open).
        if doc is not None:
            doc.set_block(
                "translations",
                {
                    "source_lang": src_lang,
                    "target_lang": tgt_lang,
                    "backend": backend or "lindat",
                    # Batch-vs-fallback telemetry deliberately does NOT go here.
                    # `translations` is a schema-governed block in a record shared
                    # across all six repos; per-run throughput counts are transient
                    # diagnostics, not a durable fact about the document. They are
                    # reported on the log stream instead (see the summary above),
                    # which is where an operator running the #46 experiment reads
                    # them. ParadataLogger has no per-document fact API to put them
                    # in — only skips, successes and components.
                    "output_mode": output_mode,
                    # With --source_lang auto, "auto" says nothing about the document;
                    # the language it was resolved to does.
                    **languages.translations_fields(),
                },
            )

        tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
        print(f"[SUCCESS] Saved ALTO translation → {output_path}")

    except Exception as e:
        print(f"\n[ERROR] Failed to process ALTO XML '{input_path}': {e}")
        raise
