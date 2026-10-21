"""
processors/lemmatizer.py – UDPipe-based lemmatizer for vocabulary term matching.

Chunking strategy
-----------------
Long texts sent to UDPipe are split at sentence boundaries before being
dispatched to the API, mirroring the approach used by the NLP-enrichment
pipeline (atrium-nlp-enrich).  This prevents mid-sentence cuts that would
confuse UDPipe's tokeniser and produce incorrect lemmas near chunk boundaries.

The splitting logic now lives in the shared ``processors/chunking.chunk_text``
helper (also used by the translator), so the two sites cannot drift apart and
the documented boundary priority is genuinely enforced.

Boundary priority (identical to the translator):
  1. Newline (OCR-line / paragraph boundary)
  2. Sentence-terminal punctuation + space
  3. Clause-level punctuation + space
  4. Word boundary (space)
  5. Hard cut – last resort

Morphological features (v0.5.2+)
--------------------------------
``_parse_conllu`` / ``get_lemmas`` remain unchanged and return ``(word, lemma)``
pairs.  A parallel pair of methods, ``_parse_conllu_with_features`` /
``get_lemmas_with_features``, additionally surfaces the CoNLL-U ``Number``
feature (``"Sing"`` / ``"Plur"`` / ``""``) as a third tuple element.  The
translator uses this to avoid freezing a singular vocabulary translation onto a
plural source token (which breaks English number agreement, e.g. "several
feature").  The original 2-tuple API is preserved so existing callers and tests
are unaffected.

Language coverage
-----------------
Only the languages in :attr:`LindatLemmatizer.MODELS` are lemmatised.  For any
other source language ``get_lemmas`` / ``get_lemmas_with_features`` return an
empty list (and warn once per language) instead of running the text through the
Czech model: Czech lemmas of, say, Hungarian tokens are noise that can still
collide with a Czech vocabulary key and freeze a wrong translation into the
output.  With no lemmas the translator's single-word pass is a no-op, and the
multi-word phrase pass (surface-form matching) still runs.

Only UDPipe's tokenizer and tagger are requested: they produce LEMMA and FEATS,
the two columns read here.  The dependency parser (HEAD/DEPREL) is never asked
for, because nothing reads its output.

Endpoint (atrium-project#63)
----------------------------
The UDPipe endpoint is an attachable backing service: pass ``url=`` explicitly,
or set ``UDPIPE_URL`` in the environment, to reach a self-hosted UDPipe 2
instance instead of LINDAT's public one.  The variable name is shared with
``atrium-nlp-enrich``, which calls the same service from its own pipeline.
"""

import logging
import os

import requests

from tool_limits import UDPIPE_TIMEOUT_S

from .chunking import chunk_text
from .limit_notes import note

logger = logging.getLogger(__name__)

#: LINDAT's public UDPipe 2 endpoint — the default, not a hard requirement.
DEFAULT_UDPIPE_URL = "https://lindat.mff.cuni.cz/services/udpipe/api/process"


def resolve_udpipe_url(url: str | None = None) -> str:
    """Resolve the UDPipe endpoint (atrium-project#63, factor IV).

    Precedence: explicit *url* argument → ``UDPIPE_URL`` in the environment →
    :data:`DEFAULT_UDPIPE_URL`. The variable name is shared with
    ``atrium-nlp-enrich``, which reaches the same service for its own pipeline;
    one service, one spelling.

    Exposed as a module function rather than inlined in ``__init__`` so that any
    caller needing to *record* the endpoint (paradata) resolves it exactly the
    way the request does, instead of re-deriving a literal that can drift.
    """
    if url is not None:
        return url.strip()
    return (os.environ.get("UDPIPE_URL", "") or DEFAULT_UDPIPE_URL).strip()


class LindatLemmatizer:
    URL = DEFAULT_UDPIPE_URL

    MODELS = {
        "cs": "czech-pdt-ud-2.15-241121",
        "sk": "slovak-snk-ud-2.15-241121",
        "pl": "polish-pdb-ud-2.15-241121",
        "de": "german-gsd-ud-2.15-241121",
        "fr": "french-gsd-ud-2.15-241121",
        "en": "english-ewt-ud-2.15-241121",
        "ru": "russian-syntagrus-ud-2.15-241121",
        "uk": "ukrainian-iu-ud-2.15-241121",
    }

    def __init__(self, url: str | None = None) -> None:
        """*url* overrides ``UDPIPE_URL``, which overrides :attr:`URL`.

        Deliberately network-free: the lemmatizer is constructed eagerly by
        ``LindatTranslator`` whenever a vocabulary loads, and the tests rely on
        construction staying hermetic.
        """
        self.url = resolve_udpipe_url(url)
        # Languages already warned about, so a long document in an unsupported
        # language logs one line rather than one per block.
        self._warned_langs: set[str] = set()

    def supports(self, lang: str) -> bool:
        """True when a UDPipe model is configured for *lang*."""
        return lang in self.MODELS

    def _model_for(self, lang: str) -> str | None:
        """The UDPipe model for *lang*, or ``None`` (warning once) when unsupported."""
        model = self.MODELS.get(lang)
        if model is None and lang not in self._warned_langs:
            self._warned_langs.add(lang)
            supported = ", ".join(sorted(self.MODELS))
            print(
                f"[WARN] No UDPipe model configured for language {lang!r} (supported: {supported}); "
                "single-word vocabulary matching is skipped for it."
            )
        return model

    def _chunk_text(self, text: str, chunk_size: int = 4000) -> list[str]:
        """
        Split *text* at sentence boundaries so that UDPipe receives complete
        sentences and produces correct lemmas across chunk edges.

        Thin wrapper around :func:`processors.chunking.chunk_text` (shared with
        the translator); retained as an instance method to preserve the
        ``self._chunk_text(...)`` call site in ``_request_conllu_chunks``.
        """
        return chunk_text(text, chunk_size)

    def get_lemmas(self, text: str, lang: str = "cs") -> list[tuple[str, str]]:
        """``(word, lemma)`` pairs for *text*; ``[]`` when *lang* has no model."""
        model = self._model_for(lang)
        if model is None:
            return []
        all_lemmas: list[tuple[str, str]] = []

        for conllu in self._request_conllu_chunks(text, model):
            all_lemmas.extend(self._parse_conllu(conllu))

        return all_lemmas

    def get_lemmas_with_features(self, text: str, lang: str = "cs") -> list[tuple[str, str, str]]:
        """
        Like :meth:`get_lemmas` but each item is ``(word, lemma, number)`` where
        *number* is ``"Sing"``, ``"Plur"`` or ``""`` (unknown / not applicable),
        read from the CoNLL-U FEATS column.

        Used by the translator to decide whether protecting a token with a
        singular vocabulary translation is safe (singular source) or would break
        agreement (plural source).  ``[]`` when *lang* has no model.
        """
        model = self._model_for(lang)
        if model is None:
            return []
        all_items: list[tuple[str, str, str]] = []

        for conllu in self._request_conllu_chunks(text, model):
            all_items.extend(self._parse_conllu_with_features(conllu))

        return all_items

    def _request_conllu_chunks(self, text: str, model: str):
        """Yield raw CoNLL-U strings for each sentence-aware chunk of *text*."""
        for chunk in self._chunk_text(text):
            if not chunk:
                continue

            try:
                resp = requests.post(
                    self.url,
                    # Tokenizer + tagger only: they fill LEMMA and FEATS, the
                    # only columns parsed below. The parser would add HEAD and
                    # DEPREL, which nothing reads, at extra server-side cost.
                    data={
                        "model": model,
                        "tokenizer": "",
                        "tagger": "",
                        "data": chunk,
                    },
                    timeout=UDPIPE_TIMEOUT_S.get(),
                )
                if resp.status_code != 200:
                    logger.warning(
                        "UDPipe returned HTTP %s; this chunk is not lemmatised and its vocabulary terms are "
                        "not protected.",
                        resp.status_code,
                    )
                    continue

                yield resp.json().get("result", "")

            except requests.exceptions.Timeout:
                # A limit that changed the result without refusing it (atrium-project#53).
                note(
                    UDPIPE_TIMEOUT_S,
                    "skipped",
                    1,
                    "a UDPipe request timed out: that chunk's vocabulary terms were translated unprotected",
                )
                logger.warning(
                    "UDPipe request timed out after %ss; this chunk's vocabulary terms are not protected.",
                    UDPIPE_TIMEOUT_S.get(),
                )
            except Exception as e:
                logger.warning("Lemmatisation failed (%s); this chunk's vocabulary terms are not protected.", e)

    @staticmethod
    def _parse_conllu(conllu: str) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for line in conllu.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3 or "-" in parts[0] or "." in parts[0]:
                continue
            word = parts[1]
            lemma = parts[2]
            results.append((word, lemma))
        return results

    @staticmethod
    def _parse_conllu_with_features(conllu: str) -> list[tuple[str, str, str]]:
        """
        Parse CoNLL-U into ``(word, lemma, number)`` triples.

        *number* is extracted from the FEATS column (index 5): ``Number=Sing`` ->
        ``"Sing"``, ``Number=Plur`` -> ``"Plur"``; absent / malformed -> ``""``.
        Token-filtering rules match ``_parse_conllu`` exactly (MWT range lines
        and empty-node lines are skipped).
        """
        results: list[tuple[str, str, str]] = []
        for line in conllu.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3 or "-" in parts[0] or "." in parts[0]:
                continue
            word = parts[1]
            lemma = parts[2]

            number = ""
            if len(parts) >= 6 and parts[5] and parts[5] != "_":
                for feat in parts[5].split("|"):
                    if feat.startswith("Number="):
                        number = feat.split("=", 1)[1]
                        break

            results.append((word, lemma, number))
        return results
