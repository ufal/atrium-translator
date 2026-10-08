"""
tests/test_ct2_translator.py – regression coverage for the CTranslate2 backend.

No model files, network access, GPU, CTranslate2 runtime, or Transformers
installation are required: the heavy modules are replaced with small fakes.
"""

import sys
import threading
from types import SimpleNamespace

import pytest

from processors.ct2_translator import CT2Translator
from processors.translator import DegenerateTranslationError, TranslationError


class _FakeTokenizer:
    eos_token = "<|im_end|>"

    def __init__(self, outputs=None, vocab=None):
        self.chat_template_calls = []
        self.tokenizer_calls = []
        self.decode_calls = []
        self.converted_tokens = []
        # Replies returned by decode(), in order; the last one repeats.
        self.outputs = list(outputs or ["Archaeological research took place in Prague's historic centre."])
        self.vocab = vocab

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        self.chat_template_calls.append((messages, tokenize, add_generation_prompt))
        assert tokenize is False
        assert add_generation_prompt is True
        return "<|im_start|>user<|im_end|><|im_start|>assistant"

    def __call__(self, prompt, *, add_special_tokens):
        self.tokenizer_calls.append((prompt, add_special_tokens))
        assert add_special_tokens is False
        return {"input_ids": [101, 102, 103]}

    def convert_ids_to_tokens(self, ids):
        return [f"tok-{i}" for i in ids]

    def convert_tokens_to_ids(self, tokens):
        self.converted_tokens.append(list(tokens))
        return list(range(200, 200 + len(tokens)))

    def get_vocab(self):
        if self.vocab is None:
            raise NotImplementedError
        return {tok: i for i, tok in enumerate(self.vocab)}

    def decode(self, ids, *, skip_special_tokens, clean_up_tokenization_spaces):
        self.decode_calls.append((ids, skip_special_tokens, clean_up_tokenization_spaces))
        return self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]


class _FakeGenerator:
    # What generate_batch returns as the generated tokens; a test may replace it.
    generated = ["out-1", "out-2"]

    def __init__(self, model_dir, *, device, compute_type):
        self.model_dir = model_dir
        self.device = device
        self.compute_type = compute_type
        self.calls = []

    def generate_batch(self, sequences, **kwargs):
        self.calls.append((sequences, kwargs))
        return [SimpleNamespace(sequences=[list(self.generated)])]


def _install_fake_modules(monkeypatch, tokenizer):
    fake_engine = _FakeGenerator
    fake_ct2 = SimpleNamespace(
        Generator=fake_engine,
        Translator=object,
        get_supported_compute_types=lambda device: {"int8"},
    )
    fake_transformers = SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda path, **kwargs: tokenizer))
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    return fake_engine


def test_eurollm_uses_hf_chat_template_and_ct2_tokens(monkeypatch):
    tokenizer = _FakeTokenizer()
    engine_cls = _install_fake_modules(monkeypatch, tokenizer)

    backend = CT2Translator(
        model_dir="/models/eurollm-ct2",
        tokenizer_dir="/models/eurollm-hf",
        family="eurollm",
        device="cuda",
        compute_type="int8",
        languages=["cs", "en"],
    )

    output = backend.translate(
        "Archeologický výzkum proběhl v centru Prahy.",
        "cs",
        "en",
    )

    assert output == "Archaeological research took place in Prague's historic centre."
    assert tokenizer.chat_template_calls[0][1:] == (False, True)
    assert ("<|im_start|>user<|im_end|><|im_start|>assistant", False) in tokenizer.tokenizer_calls
    # clean_up_tokenization_spaces is a WordPiece post-process; on EuroLLM's BPE
    # vocabulary it would delete spaces the model wrote before punctuation.
    assert tokenizer.decode_calls[0][1:] == (True, False)

    # The CTranslate2 engine receives token strings, never the characters of the
    # prompt. The generated end token is the model's actual EOS token.
    engine = backend._engine
    assert isinstance(engine, engine_cls)
    assert engine.calls[0][0] == [["tok-101", "tok-102", "tok-103"]]
    assert engine.calls[0][1]["end_token"] == "<|im_end|>"
    assert engine.calls[0][1]["include_prompt_in_result"] is False


def test_eurollm_does_not_load_sentencepiece_even_when_ct2_sp_model_is_set(
    monkeypatch,
):
    tokenizer = _FakeTokenizer()
    _install_fake_modules(monkeypatch, tokenizer)

    fake_sp = SimpleNamespace(
        SentencePieceProcessor=lambda **kwargs: pytest.fail("SentencePiece must not be loaded for EuroLLM")
    )
    monkeypatch.setitem(sys.modules, "sentencepiece", fake_sp)

    backend = CT2Translator(
        model_dir="/models/eurollm-ct2",
        tokenizer_dir="/models/eurollm-hf",
        family="eurollm",
        sp_model="/some/sentencepiece.model",
        device="cuda",
        compute_type="int8",
    )
    backend.translate("Archeologický výzkum proběhl v Praze.", "cs", "en")


def _eurollm(monkeypatch, tokenizer, vocabulary=None):
    _install_fake_modules(monkeypatch, tokenizer)
    backend = CT2Translator(
        model_dir="/models/eurollm-ct2", tokenizer_dir="/models/eurollm-hf", family="eurollm", languages=["cs", "en"]
    )
    backend.vocabulary = dict(vocabulary or {})
    return backend


_POINT = "Navigační bod: N 49°52'50.18\", E 14°23'26.70\" (severní okraj klášterního areálu na ostrově)."
_POINT_EN = "Navigation point: N 49°52'50.18\", E 14°23'26.70\" (northern edge of the monastery area on the island)."


def test_eurollm_prompt_names_the_languages_and_keeps_the_glossary_out_of_the_text(monkeypatch):
    tokenizer = _FakeTokenizer(outputs=[_POINT_EN])
    backend = _eurollm(monkeypatch, tokenizer, {"bod": "point"})

    assert backend.translate(_POINT, "cs", "en") == _POINT_EN

    [(messages, _, _)] = tokenizer.chat_template_calls
    system, user = messages
    assert system["role"] == "system" and user["role"] == "user"
    assert "bod = point" in system["content"]
    # EuroLLM-Instruct's own translation format, languages in words.
    assert user["content"] == f"Translate the following Czech source text to English:\nCzech: {_POINT} \nEnglish: "
    assert "bod = point" not in user["content"]
    assert backend.protected_count == 1


def test_eurollm_prompt_without_a_vocabulary_has_no_terminology_block(monkeypatch):
    tokenizer = _FakeTokenizer(outputs=[_POINT_EN])
    backend = _eurollm(monkeypatch, tokenizer)
    backend.translate(_POINT, "cs", "en")
    system = tokenizer.chat_template_calls[0][0][0]["content"]
    assert "Terminology" not in system
    assert backend.protected_count == 0


@pytest.mark.parametrize(
    "reply",
    [
        # 2026-09-27 EuroLLM-1.7B AMCR run, C-N1000019 poznamka (old prompt wording)
        f"Use these exact terms: point = point\n\n{_POINT_EN}",
        # the terminology block of this prompt, written back in full
        f"Terminology — when one of these Czech terms occurs in the text, translate it as given:\nbod = point\n\n{_POINT_EN}",
        # pairs without the header (C-N9000080 popis)
        f"construction = construction\n\n{_POINT_EN}",
        # the prompt's own labels
        f"Czech: {_POINT}\nEnglish: {_POINT_EN}",
        f"English: {_POINT_EN}",
        # the glossary after the translation
        f"{_POINT_EN}\n\nbod = point",
    ],
)
def test_a_prompt_echoed_around_the_translation_is_removed(monkeypatch, reply):
    tokenizer = _FakeTokenizer(outputs=[reply])
    backend = _eurollm(monkeypatch, tokenizer, {"bod": "point"})

    assert backend.translate(_POINT, "cs", "en") == _POINT_EN
    assert len(backend._engine.calls) == 1


@pytest.mark.parametrize(
    "reply",
    [
        # C-DT-100003326 popis: the reply was the glossary and nothing else
        "Terrain edge = terrain edge; Hillfort = hillfort",
        # C-9107617A: header, then the whole field as one more "term = term" pair
        "Use these exact terms: jáma = pit\n\njáma vyplněná mazanicí = pit filled with cement",
        # C-9124095A: header, then the Czech source copied unchanged
        "Use these exact terms: trade good\n\nWaldenburské zboží, zlomky z lahví na minerální vodu",
    ],
)
def test_a_reply_that_is_only_the_glossary_is_re_requested_without_it(monkeypatch, reply):
    source = "Waldenburské zboží, zlomky z lahví na minerální vodu"
    english = "Waldenburg ware, fragments of mineral water bottles"
    tokenizer = _FakeTokenizer(outputs=[reply, english])
    backend = _eurollm(monkeypatch, tokenizer, {"zboží": "trade good", "jáma": "pit"})

    assert backend.translate(source, "cs", "en") == english

    first, second = (call[0][0]["content"] for call in tokenizer.chat_template_calls)
    assert "Terminology" in first
    assert "Terminology" not in second
    assert backend.protected_count == 0  # the glossary was not what produced the translation


def test_a_source_copied_back_is_rejected_when_the_retry_copies_it_too(monkeypatch):
    source = "Waldenburské zboží, zlomky z lahví na minerální vodu"
    tokenizer = _FakeTokenizer(outputs=[source])
    backend = _eurollm(monkeypatch, tokenizer, {"zboží": "trade good"})

    with pytest.raises(DegenerateTranslationError, match="untranslated"):
        backend.translate(source, "cs", "en")
    assert len(backend._engine.calls) == 2


def test_a_list_of_names_may_come_back_unchanged(monkeypatch):
    names = "Baier, Kaiser, Bařinka, Švácha"
    tokenizer = _FakeTokenizer(outputs=[names])
    backend = _eurollm(monkeypatch, tokenizer)
    assert backend.translate(names, "cs", "en") == names


def test_an_equals_sign_that_the_source_has_is_kept(monkeypatch):
    source = "Měřítko 1 : 100, poměr stran a = b"
    english = "Scale 1 : 100, aspect ratio a = b"
    tokenizer = _FakeTokenizer(outputs=[english])
    backend = _eurollm(monkeypatch, tokenizer)
    assert backend.translate(source, "cs", "en") == english


def test_generation_stops_at_the_chat_turn_end_and_the_next_turn_is_dropped(monkeypatch):
    # A tokenizer that names </s> as EOS while the chat template closes turns with <|im_end|>.
    tokenizer = _FakeTokenizer(vocab=["</s>", "<|im_start|>", "<|im_end|>", "a"])
    tokenizer.eos_token = "</s>"
    backend = _eurollm(monkeypatch, tokenizer)
    monkeypatch.setattr(_FakeGenerator, "generated", ["The", "castle", "<|im_end|>", "<|im_start|>", "user"])

    backend.translate("Hrad stojí na kopci.", "cs", "en")

    kwargs = backend._engine.calls[0][1]
    assert kwargs["end_token"] == ["</s>", "<|im_end|>", "<|im_start|>"]
    assert tokenizer.converted_tokens[-1] == ["The", "castle"]


def test_end_token_is_the_eos_alone_when_the_tokenizer_has_no_vocab_listing(monkeypatch):
    tokenizer = _FakeTokenizer()
    backend = _eurollm(monkeypatch, tokenizer)
    backend.translate("Archeologický výzkum proběhl v centru Prahy.", "cs", "en")
    assert backend._engine.calls[0][1]["end_token"] == "<|im_end|>"


def test_a_reply_at_the_decoding_cap_without_a_turn_end_is_rejected(monkeypatch):
    monkeypatch.setenv("CT2_MAX_DECODING_TOKENS", "3")
    tokenizer = _FakeTokenizer()
    backend = _eurollm(monkeypatch, tokenizer)
    monkeypatch.setattr(_FakeGenerator, "generated", ["a", "b", "c"])
    with pytest.raises(DegenerateTranslationError, match="CT2_MAX_DECODING_TOKENS=3"):
        backend.translate("Archeologický výzkum proběhl v centru Prahy.", "cs", "en")


def test_the_tokenizer_comes_from_the_model_dir_when_the_converter_copied_it(tmp_path):
    (tmp_path / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    backend = CT2Translator(model_dir=str(tmp_path), family="eurollm")
    assert backend._tokenizer_source() == str(tmp_path)


def test_the_tokenizer_falls_back_to_the_checkpoint_of_ct2_sp_model(tmp_path):
    """The #46 recipe converts without --copy_files and points CT2_SP_MODEL at the checkpoint's tokenizer.model."""
    model_dir, hf_dir = tmp_path / "ct2", tmp_path / "hf"
    model_dir.mkdir()
    hf_dir.mkdir()
    (hf_dir / "tokenizer.model").write_bytes(b"")
    (hf_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    backend = CT2Translator(model_dir=str(model_dir), sp_model=str(hf_dir / "tokenizer.model"), family="eurollm")
    assert backend._tokenizer_source() == str(hf_dir)


def test_no_tokenizer_anywhere_names_ct2_tokenizer_dir(tmp_path):
    backend = CT2Translator(model_dir=str(tmp_path), family="eurollm")
    with pytest.raises(TranslationError, match="CT2_TOKENIZER_DIR"):
        backend._tokenizer_source()


def test_an_explicit_tokenizer_dir_is_used_as_given():
    backend = CT2Translator(model_dir="/models/ct2", tokenizer_dir="/models/hf", family="eurollm")
    assert backend._tokenizer_source() == "/models/hf"


def test_a_missing_jinja2_is_a_translation_error(monkeypatch):
    tokenizer = _FakeTokenizer()

    def _no_jinja(*args, **kwargs):
        raise ImportError("apply_chat_template requires jinja2 to be installed")

    tokenizer.apply_chat_template = _no_jinja
    backend = _eurollm(monkeypatch, tokenizer)
    with pytest.raises(TranslationError, match="jinja2"):
        backend.translate("Archeologický výzkum proběhl v centru Prahy.", "cs", "en")


class _EchoSp:
    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)


class _EchoEngine:
    def __init__(self):
        self.calls = []

    def translate_batch(self, batch, **kwargs):
        self.calls.append((batch[0], kwargs))
        prefix = kwargs.get("target_prefix", [[]])[0]
        return [SimpleNamespace(hypotheses=[prefix + ["Hrad", "stojí", "</s>"]])]


def _nmt(family):
    backend = CT2Translator(model_dir="/models/nmt", family=family)
    backend._sp, backend._engine = _EchoSp(), _EchoEngine()
    backend._guard = lambda source, translated: None
    return backend


@pytest.mark.parametrize(
    ("family", "source", "prefix"),
    [
        ("opus", ["Hrad", "stojí", "</s>"], None),
        ("madlad", ["<2en>", "Hrad", "stojí", "</s>"], None),
        ("nllb", ["ces_Latn", "Hrad", "stojí", "</s>"], [["eng_Latn"]]),
    ],
)
def test_nmt_input_is_what_the_checkpoint_tokenizer_would_produce(family, source, prefix):
    backend = _nmt(family)
    assert backend._translate_nmt("Hrad stojí", "cs", "en") == "Hrad stojí"
    sent, kwargs = backend._engine.calls[0]
    assert sent == source
    assert kwargs.get("target_prefix") == prefix


def test_the_decoding_budget_follows_the_source_length(monkeypatch):
    # The fake tokenizer gives every text 3 tokens: 4 * 3 + 32 = 44, under the 2048 cap.
    tokenizer = _FakeTokenizer()
    backend = _eurollm(monkeypatch, tokenizer)
    backend.translate("Archeologický výzkum proběhl v centru Prahy.", "cs", "en")
    assert backend._engine.calls[0][1]["max_length"] == 44


def test_a_reply_still_going_at_the_budget_is_a_runaway(monkeypatch):
    tokenizer = _FakeTokenizer()
    backend = _eurollm(monkeypatch, tokenizer)
    monkeypatch.setattr(_FakeGenerator, "generated", ["zlomky"] * 44)
    with pytest.raises(DegenerateTranslationError, match="runaway length: still going at 44 tokens"):
        backend.translate("zlomky", "cs", "en")


# ── the end-of-document re-run decodes differently (translator#4, 2026-09-28) ───────────
#
# Decoding is deterministic, so a flagged segment asked for again after the cool-down came back
# exactly as it had: the re-run could not recover it. `utils` enters `retrying(round)` for the
# re-run's requests, and the search is wider inside it. That the wider search cures what it was
# added for is not shown here: the stub engines below only record how they were called.


def _search_options(backend):
    """The decode options of every generation the engine saw, in order."""
    return [
        {k: v for k, v in kwargs.items() if k in ("beam_size", "sampling_temperature")}
        for _, kwargs in backend._engine.calls
    ]


def test_eurollm_is_greedy_on_the_first_request_and_searches_a_widening_beam_on_a_rerun(monkeypatch):
    backend = _eurollm(monkeypatch, _FakeTokenizer())
    text = "Archeologický výzkum proběhl v centru Prahy."
    backend.translate(text, "cs", "en")
    with backend.retrying(1):
        backend.translate(text, "cs", "en")
    with backend.retrying(2):
        backend.translate(text, "cs", "en")
    backend.translate(text, "cs", "en")  # outside the block again
    assert _search_options(backend) == [
        {"sampling_temperature": 0.0},
        {"beam_size": 4},
        {"beam_size": 6},
        {"sampling_temperature": 0.0},
    ]


def test_the_nmt_beam_widens_with_each_round_up_to_a_cap():
    backend = _nmt("opus")
    beams = []
    for rerun_round in (0, 1, 2, 3, 9):
        with backend.retrying(rerun_round):
            backend._translate_nmt("Hrad stojí", "cs", "en")
        beams.append(backend._engine.calls[-1][1]["beam_size"])
    assert beams == [4, 6, 8, 10, 12]


def test_the_rerun_round_is_per_thread_and_restored_on_leaving_the_block():
    backend = _nmt("opus")
    seen = {}

    def other_thread():
        # The main thread is inside retrying(2); this one is serving another request.
        backend._translate_nmt("Hrad stojí", "cs", "en")
        seen["beam"] = backend._engine.calls[-1][1]["beam_size"]

    with backend.retrying(2):
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
        with backend.retrying(1):
            assert backend._rerun_round() == 1
        assert backend._rerun_round() == 2, "a nested block gives the outer round back"
    assert seen["beam"] == 4
    assert backend._rerun_round() == 0

    with pytest.raises(RuntimeError):
        with backend.retrying(3):
            raise RuntimeError("the request failed")
    assert backend._rerun_round() == 0, "a failed request does not leave the round set"


# ── warm-up and one load under concurrency (the service's first requests) ───────────────


def test_warm_loads_the_engine_and_the_eurollm_tokenizer(monkeypatch):
    tokenizer = _FakeTokenizer()
    backend = _eurollm(monkeypatch, tokenizer)
    backend.warm()
    assert backend._engine is not None
    assert backend._tokenizer is tokenizer


def test_concurrent_first_requests_load_the_model_once(monkeypatch):
    import threading
    import time

    tokenizer = _FakeTokenizer()
    _install_fake_modules(monkeypatch, tokenizer)
    loads = []

    class _SlowGenerator(_FakeGenerator):
        def __init__(self, *args, **kwargs):
            loads.append(1)
            time.sleep(0.05)  # long enough for the other threads to arrive
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sys.modules["ctranslate2"], "Generator", _SlowGenerator)
    backend = CT2Translator(model_dir="/m", tokenizer_dir="/hf", family="eurollm")
    threads = [threading.Thread(target=backend.warm) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1


def _run_lifespan(monkeypatch, backend):
    import asyncio
    from contextlib import asynccontextmanager

    import service.api as api

    @asynccontextmanager
    async def _no_lifecycle(state):
        yield

    monkeypatch.setattr(api, "get_backend", lambda *a, **k: backend)
    monkeypatch.setattr(api, "LanguageIdentifier", lambda: None)
    monkeypatch.setattr(api, "serve_lifecycle", _no_lifecycle)
    monkeypatch.setattr(api._state, "warm", False)

    async def _enter():
        async with api.lifespan(api.app):
            return api._state.warm

    return asyncio.run(_enter())


def test_the_service_loads_a_ct2_model_at_startup(monkeypatch):
    tokenizer = _FakeTokenizer()
    backend = _eurollm(monkeypatch, tokenizer)
    assert _run_lifespan(monkeypatch, backend) is True  # /ready's flag, set after the load
    assert backend._engine is not None and backend._tokenizer is tokenizer


def test_a_ct2_configuration_error_stops_the_service_at_startup(monkeypatch):
    backend = CT2Translator(model_dir="", family="eurollm")  # CT2_MODEL_DIR unset
    with pytest.raises(TranslationError, match="CT2_MODEL_DIR"):
        _run_lifespan(monkeypatch, backend)
