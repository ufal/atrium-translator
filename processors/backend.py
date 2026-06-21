"""
processors/backend.py – Pluggable translation backend interface.

Defines a ``TranslationBackend`` Protocol that captures the contract the
pipeline already depends on (``translate``).  A ``get_backend`` factory returns
the configured implementation.

The default backend is ``"lindat"`` (the existing ``LindatTranslator``).
``LindatTranslator`` structurally satisfies the Protocol without any
modifications to ``processors/translator.py``.

See ``docs/translation-backends.md`` for the full evaluation of candidate
backends and integration guidance.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class TranslationBackend(Protocol):
    """Minimal contract that every translation backend must satisfy.

    Only ``translate`` is required — it is the single coupling point used by
    ``utils.process_alto_xml``, ``utils.process_metadata_xml``,
    ``service/api.py``, and ``main.process_single_file``.

    ``LindatTranslator`` already implements this structurally.
    """

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        ...


_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> None:
    if _REGISTRY:
        return
    from .translator import LindatTranslator
    _REGISTRY["lindat"] = LindatTranslator


def get_backend(name: str | None = None, **kwargs) -> TranslationBackend:
    """Return a ``TranslationBackend`` instance for *name*.

    *name* defaults to the ``TRANSLATION_BACKEND`` environment variable, then
    to ``"lindat"``.  Unknown names raise ``ValueError``.

    *kwargs* are forwarded to the backend constructor (e.g. ``vocab_path``
    for ``LindatTranslator``).
    """
    _ensure_registry()
    if name is None:
        name = os.environ.get("TRANSLATION_BACKEND", "lindat")
    name = name.lower().strip()
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown translation backend {name!r}. "
            f"Available: {available}. "
            f"See docs/translation-backends.md for integration guidance."
        )
    return _REGISTRY[name](**kwargs)
