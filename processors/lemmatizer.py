"""
processors/lemmatizer.py – UDPipe-based lemmatizer for vocabulary term matching.

Chunking strategy
-----------------
Long texts sent to UDPipe are split at sentence boundaries before being
dispatched to the API, mirroring the approach used by the NLP-enrichment
pipeline (atrium-nlp-enrich).  This prevents mid-sentence cuts that would
confuse UDPipe's tokeniser and produce incorrect lemmas near chunk boundaries.

Boundary priority (identical to translator._chunk_text):
  1. Newline (OCR-line / paragraph boundary)
  2. Sentence-terminal punctuation + space
  3. Clause-level punctuation + space
  4. Word boundary (space)
  5. Hard cut – last resort
"""

import requests


class LindatLemmatizer:
    URL = "https://lindat.mff.cuni.cz/services/udpipe/api/process"

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
    DEFAULT_MODEL = "czech-pdt-ud-2.15-241121"

    # Sentence / clause boundaries, same priority as translator._chunk_text
    _SEPS: list[tuple[str, int]] = [
        ("\n",  0),
        (". ",  1),
        ("! ",  1),
        ("? ",  1),
        ("; ",  1),
        (", ",  1),
    ]

    def _chunk_text(self, text: str, chunk_size: int = 4000) -> list[str]:
        """
        Split *text* at sentence boundaries so that UDPipe receives complete
        sentences and produces correct lemmas across chunk edges.

        Boundary priority:
          1. ``\\n``  – paragraph / OCR-line boundary
          2. Sentence-terminal punctuation + space
          3. Clause-level punctuation + space
          4. Word boundary
          5. Hard cut (last resort)
        """
        if not text or not text.strip():
            return []
        text = text.strip()
        if len(text) <= chunk_size:
            return [text]

        _MIN_SPLIT = chunk_size // 4

        chunks: list[str] = []
        remaining = text

        while len(remaining) > chunk_size:
            window = remaining[:chunk_size]
            best = -1

            for sep, keep in self._SEPS:
                pos = window.rfind(sep)
                if pos > _MIN_SPLIT:
                    candidate = pos + keep
                    if candidate > best:
                        best = candidate

            # Fallback: word boundary
            if best <= _MIN_SPLIT:
                pos = window.rfind(" ")
                best = pos if pos > 0 else chunk_size

            chunks.append(remaining[:best].strip())
            remaining = remaining[best:].lstrip()

        if remaining.strip():
            chunks.append(remaining.strip())

        return [c for c in chunks if c]

    def get_lemmas(self, text: str, lang: str = "cs") -> list[tuple[str, str]]:
        model = self.MODELS.get(lang, self.DEFAULT_MODEL)
        all_lemmas: list[tuple[str, str]] = []

        for chunk in self._chunk_text(text):
            if not chunk:
                continue

            try:
                resp = requests.post(
                    self.URL,
                    data={
                        "model":     model,
                        "tokenizer": "",
                        "tagger":    "",
                        "parser":    "",
                        "data":      chunk,
                    },
                    timeout=30,
                )
                if resp.status_code != 200:
                    print(
                        f"[WARN] UDPipe returned HTTP {resp.status_code}; "
                        "skipping lemmatisation for chunk."
                    )
                    continue

                conllu = resp.json().get("result", "")
                all_lemmas.extend(self._parse_conllu(conllu))

            except requests.exceptions.Timeout:
                print("[WARN] UDPipe request timed out; skipping lemmatisation for chunk.")
            except Exception as e:
                print(f"[WARN] Lemmatisation failed: {e}")

        return all_lemmas

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
            word  = parts[1]
            lemma = parts[2]
            results.append((word, lemma))
        return results