"""
processors/lemmatizer.py – UDPipe-based lemmatizer for vocabulary term matching.

Calls the LINDAT UDPipe API to retrieve (word, lemma) pairs from source text,
enabling accurate lemma-based vocabulary lookups regardless of inflected forms.
"""

import requests


class LindatLemmatizer:
    """
    Wraps the LINDAT UDPipe REST API to perform tokenisation + lemmatisation.

    The model identifier matches the one used in the atrium-nlp-enrich pipeline
    (config_api.txt: MODEL_UDPIPE = czech-pdt-ud-2.15-241121).
    """

    URL = "https://lindat.mff.cuni.cz/services/udpipe/api/process"

    # Language → UDPipe model mapping (extend as needed)
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

    def get_lemmas(self, text: str, lang: str = "cs") -> list[tuple[str, str]]:
        """
        Returns a list of ``(word, lemma)`` tuples for every token in *text*.

        Falls back to an empty list on any network or parse error so that the
        caller can gracefully degrade to untokenised translation.

        Parameters
        ----------
        text : str
            Raw source text (one or more sentences).
        lang : str
            ISO 639-1 language code used to select the UDPipe model.
        """
        model = self.MODELS.get(lang, self.DEFAULT_MODEL)
        try:
            resp = requests.post(
                self.URL,
                data={
                    "model":     model,
                    "tokenizer": "",   # enable tokeniser
                    "tagger":    "",   # enable tagger/lemmatiser
                    "parser":    "",   # skip dependency parsing (faster)
                    "data":      text,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"[WARN] UDPipe returned HTTP {resp.status_code}; skipping lemmatisation.")
                return []

            conllu = resp.json().get("result", "")
            return self._parse_conllu(conllu)

        except requests.exceptions.Timeout:
            print("[WARN] UDPipe request timed out; skipping lemmatisation.")
            return []
        except Exception as e:
            print(f"[WARN] Lemmatisation failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_conllu(conllu: str) -> list[tuple[str, str]]:
        """
        Parses CoNLL-U output and extracts ``(word, lemma)`` pairs.

        CoNLL-U columns (1-indexed):
          1  ID   2  FORM   3  LEMMA   4  UPOS   5  XPOS
          6  FEATS  7  HEAD  8  DEPREL  9  DEPS  10  MISC
        We need columns 2 (FORM / surface word) and 3 (LEMMA).
        """
        results: list[tuple[str, str]] = []
        for line in conllu.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            # Skip multi-word tokens (ID contains '-') and empty nodes (ID contains '.')
            if len(parts) < 3 or "-" in parts[0] or "." in parts[0]:
                continue
            word  = parts[1]
            lemma = parts[2]
            results.append((word, lemma))
        return results