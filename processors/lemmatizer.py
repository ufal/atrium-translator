"""
processors/lemmatizer.py – UDPipe-based lemmatizer for vocabulary term matching.
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

    def _chunk_text(self, text: str, chunk_size: int = 4000) -> list:
        """Space-aware chunking to prevent Payload Too Large errors."""
        chunks = []
        while len(text) > chunk_size:
            split_idx = text.rfind(" ", 0, chunk_size)
            if split_idx == -1:
                split_idx = chunk_size
            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
        if text:
            chunks.append(text)
        return chunks

    def get_lemmas(self, text: str, lang: str = "cs") -> list[tuple[str, str]]:
        model = self.MODELS.get(lang, self.DEFAULT_MODEL)
        all_lemmas = []

        # FIX: Iterate over text chunks to prevent 413 Payload Too Large
        for chunk in self._chunk_text(text):
            if not chunk: continue

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
                    print(f"[WARN] UDPipe returned HTTP {resp.status_code}; skipping lemmatisation for chunk.")
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