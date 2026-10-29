"""tool_limits.py — every limit atrium-translator has (atrium-project#53, factor III).

One declaration, read by the batch CLI (``main.py``), the processors and the service, and
reported by ``GET /info`` (``limits`` and ``limits_meta``). Each limit is an environment
setting; a malformed value stops the process at startup, naming the variable
(``atrium_limits.LimitConfigError``). ``.env.example`` and ``service/README.md``'s
``## Limits`` table list the same set, and ``tests/test_limits_contract.py`` checks that
they agree. Values are read on every use, so a changed variable takes effect without a
restart of a long-running process and a test can set one.

What happens over each limit — refused, or processed in full with a ``limits_applied``
note — is said beside it. The LLM and CT2 limits apply only when that backend is the one
``TRANSLATION_BACKEND`` selects; they are declared (and reported) regardless, so ``/info``
does not depend on which backend happens to be imported.

Standard library only (``atrium_limits`` is the hub's canonical module at the repo root).
"""

from __future__ import annotations

from atrium_limits import LimitSet, limit, upload_limit

#: §4.5 upload limit, per uploaded part (the XML, and the baseline document JSON).
#: Over it → 413 ``limit_exceeded``.
MAX_UPLOAD = upload_limit(50)

#: Longest text sent to a backend in one request. A longer segment is split at a line,
#: sentence, clause or word boundary and translated in full, in pieces re-joined with a
#: line break → ``split`` note.
TRANSLATION_CHUNK_CHARS = limit("TRANSLATION_CHUNK_CHARS", 4000, unit="chars", minimum=100)

#: How much of one segment (ALTO block, metadata field) the language identifier reads
#: with ``source_lang=auto``. A longer segment's language is decided on its first N
#: characters → ``sampled`` note.
LANG_ID_SEGMENT_CHARS = limit("LANG_ID_SEGMENT_CHARS", 2000, unit="chars", minimum=1)

#: How much of the whole document the identifier reads to decide the document language
#: (the context every segment falls back to). A longer document → ``sampled`` note.
LANG_ID_DOCUMENT_CHARS = limit("LANG_ID_DOCUMENT_CHARS", 20000, unit="chars", minimum=1)

#: Per-request timeout of one LINDAT translation call; a timeout is retried.
LINDAT_TIMEOUT_S = limit("LINDAT_TIMEOUT_S", 60, unit="s", kind=float, minimum=1)

#: Retries of one LINDAT call on a network error or HTTP 429/5xx. Once they run out the
#: whole file fails → 500.
LINDAT_MAX_RETRIES = limit("LINDAT_MAX_RETRIES", 4, unit="retries")

#: Re-requests of a LINDAT reply that came back HTTP 200 but degenerate. Once they run out
#: the segment is flagged for the end-of-document re-run.
LINDAT_GUARD_RETRIES = limit("LINDAT_GUARD_RETRIES", 2, unit="retries")

#: End-of-document re-run rounds for flagged segments (0 disables the re-run). A segment
#: still degenerate after them keeps its source text → ``skipped`` note.
TRANSLATION_RERUN_ROUNDS = limit("TRANSLATION_RERUN_ROUNDS", 1, unit="rounds")

#: Per-request timeout of one UDPipe call — the lemma matching of Tag-and-Protect (LINDAT
#: backend with a vocabulary). A chunk whose request times out is not lemmatised, so its
#: vocabulary terms are translated unprotected → ``skipped`` note.
UDPIPE_TIMEOUT_S = limit("UDPIPE_TIMEOUT_S", 30, unit="s", kind=float, minimum=1)
#: Per-request timeout of one LLM call (``TRANSLATION_BACKEND=openai_compatible``).
LLM_TIMEOUT_S = limit("LLM_TIMEOUT_S", 120, unit="s", kind=float, minimum=1)

#: Output-token cap of one LLM reply. A reply the provider cut at it
#: (``finish_reason == "length"``) is treated as degenerate, never used as a translation.
LLM_MAX_TOKENS = limit("LLM_MAX_TOKENS", 2048, unit="tokens", minimum=1)

#: Retries of one LLM call on a network error or HTTP 429/5xx.
LLM_MAX_RETRIES = limit("LLM_MAX_RETRIES", 4, unit="retries")

#: Vocabulary terms injected into one LLM prompt (with ``--vocabulary`` or ``VOCABULARY_PATH``). More matches than
#: this → the longest ones are kept → ``trimmed`` note.
LLM_MAX_GLOSSARY_TERMS = limit("LLM_MAX_GLOSSARY_TERMS", 40, unit="terms")

#: Longest concept description given to the LLM beside a glossary term, as context for
#: choosing the term's sense (the vocabulary CSV's ``description`` column, translator#4).
#: 0, the default, gives the model no descriptions. A longer one is cut at a word
#: → ``trimmed`` note.
LLM_GLOSSARY_DESCRIPTION_CHARS = limit("LLM_GLOSSARY_DESCRIPTION_CHARS", 0, unit="chars", minimum=0)

#: Longest input one CTranslate2 NMT request may have. A longer chunk is re-split and
#: translated in full → ``split`` note (CTranslate2 would otherwise drop the rest silently).
CT2_MAX_INPUT_TOKENS = limit("CT2_MAX_INPUT_TOKENS", 1024, unit="tokens", minimum=16)

#: Output-token cap of one CTranslate2 reply. A reply that reaches it is treated as
#: degenerate, never used as a translation.
CT2_MAX_DECODING_TOKENS = limit("CT2_MAX_DECODING_TOKENS", 2048, unit="tokens", minimum=1)

#: Vocabulary terms injected into one CTranslate2 EuroLLM prompt (with ``--vocabulary`` or ``VOCABULARY_PATH``).
#: More matches → the longest are kept → ``trimmed`` note.
CT2_MAX_GLOSSARY_TERMS = limit("CT2_MAX_GLOSSARY_TERMS", 40, unit="terms")

LIMITS = LimitSet(
    MAX_UPLOAD,
    TRANSLATION_CHUNK_CHARS,
    LANG_ID_SEGMENT_CHARS,
    LANG_ID_DOCUMENT_CHARS,
    LINDAT_TIMEOUT_S,
    LINDAT_MAX_RETRIES,
    LINDAT_GUARD_RETRIES,
    TRANSLATION_RERUN_ROUNDS,
    UDPIPE_TIMEOUT_S,
    LLM_TIMEOUT_S,
    LLM_MAX_TOKENS,
    LLM_MAX_RETRIES,
    LLM_MAX_GLOSSARY_TERMS,
    LLM_GLOSSARY_DESCRIPTION_CHARS,
    CT2_MAX_INPUT_TOKENS,
    CT2_MAX_DECODING_TOKENS,
    CT2_MAX_GLOSSARY_TERMS,
)


def max_request_mb() -> float:
    """Ceiling on a whole /translate request: two parts at the upload limit plus 1 MiB of
    multipart overhead. Derived from MAX_UPLOAD_MB; over it → 413 ``limit_exceeded``."""
    return round(2 * MAX_UPLOAD.get() + 1, 3)


LIMITS.derived("max_request_mb", max_request_mb, unit="MB", derived_from=["MAX_UPLOAD_MB"])
