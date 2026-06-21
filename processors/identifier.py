import fasttext
from huggingface_hub import hf_hub_download


class LanguageIdentifier:
    # Map ISO 639-3 (FastText) to ISO 639-1 (Lindat)
    CODE_MAP = {
        "ces": "cs",
        "eng": "en",
        "fra": "fr",
        "deu": "de",
        "rus": "ru",
        "pol": "pl",
        "ukr": "uk",
        "slk": "sk",
        "bul": "bg",
        "hrv": "hr",
        "slv": "sl",
        "lav": "lv",
        "lit": "lt",
        "est": "et",
        "hun": "hu",
        "ron": "ro",
        "spa": "es",
        "ita": "it",
        "nld": "nl",
        "hin": "hi",
    }

    def __init__(self):
        """
        Initializes the LanguageIdentifier by downloading and loading
        the FastText language identification model from Hugging Face Hub.
        """
        try:
            model_path = hf_hub_download(repo_id="facebook/fasttext-language-identification", filename="model.bin")
            self.model = fasttext.load_model(model_path)
        except Exception as e:
            print(f"[ERROR] Failed to load FastText language model: {type(e).__name__} - {e}")
            self.model = None

    def detect(self, text):
        """
        Detects the language of the provided text. Normalizes text structure,
        queries the FastText model, and maps the ISO 639-3 result to ISO 639-1.
        Returns a tuple: (language_code, confidence_score).
        """
        if not self.model:
            print("[WARN] Language identification model is not loaded. Defaulting to 'en'.")
            return "en", 0.0

        if not text or not text.strip():
            return "en", 0.0

        # Lowercase for better detection
        clean_text = text.replace("\n", " ").lower()[:2000]

        try:
            labels, scores = self.model.predict(clean_text)

            raw_label = labels[0].replace("__label__", "")
            iso3_code = raw_label.split("_")[0]
            lang_code = self.CODE_MAP.get(iso3_code, iso3_code)

            score = scores[0]
            return lang_code, score
        except Exception as e:
            print(f"[ERROR] Language detection prediction failed: {type(e).__name__} - {e}")
            return "en", 0.0
