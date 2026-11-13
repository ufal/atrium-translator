"""
tests/test_ct2_backend.py – Hermetic tests for the CTranslate2 self-host backend
(processors/ct2_translator.py, issue #4 Phase 3).

No ctranslate2, no sentencepiece, no model, no network: only behaviour that does
not require the (lazily loaded) engine is exercised here.
"""

import sys
import types

import pytest

from atrium_paradata import _load_para_config
from atrium_test_support import REPO_ROOT
from processors import ct2_translator
from processors.backend import TranslationBackend
from processors.ct2_translator import CT2Translator
from processors.translator import TranslationError


def test_name_and_protocol():
    b = CT2Translator(model_dir="/tmp/fake", family="eurollm")
    assert b.name == "ct2"
    assert isinstance(b, TranslationBackend)


def test_eurollm_supports_glossary_madlad_does_not():
    assert CT2Translator(model_dir="/x", family="eurollm").supports_glossary is True
    assert CT2Translator(model_dir="/x", family="madlad").supports_glossary is False
    assert CT2Translator(model_dir="/x", family="nllb").supports_glossary is False


def test_pipeline_compat_surface_present():
    b = CT2Translator(model_dir="/x")
    assert hasattr(b, "vocabulary")
    assert callable(b.reset_protected_count)
    assert isinstance(b.protected_count, int)
    assert callable(b.license_components)


def test_trivial_short_circuits_need_no_engine():
    b = CT2Translator(model_dir="/x")
    assert b.translate("   ", "cs", "en") == "   "
    assert b.translate("text", "en", "en") == "text"


def test_missing_model_dir_raises_on_translate():
    b = CT2Translator(model_dir="")
    with pytest.raises(TranslationError, match="not configured"):
        b.translate("Ahoj", "cs", "en")


def test_license_components_permissive_stack():
    eurollm = CT2Translator(model_dir="/x", family="eurollm")
    assert eurollm.license_components(False) == ["ctranslate2", "eurollm"]
    assert eurollm.license_components(True) == ["ctranslate2", "eurollm", "amcr_vocab", "teater_data"]
    madlad = CT2Translator(model_dir="/x", family="madlad")
    assert madlad.license_components(False) == ["ctranslate2", "madlad400"]
    # An NMT family has no glossary: a loaded vocabulary is never used, so it is not recorded.
    assert madlad.license_components(True) == ["ctranslate2", "madlad400"]


def _terms(components):
    """Each component of a run with the licence para_config.txt declares for it: the facts the
    paradata list (paradata 3.0, atrium-project#84). Nothing is combined into one licence."""
    declared = {c["name"]: c["license"] for c in _load_para_config(str(REPO_ROOT))["components"]}
    return {c: declared[c] for c in components}


def test_the_amcr_and_teater_vocabularies_are_cc0():
    """(atrium-project#6, AMCR 2026-09-28) the heslo set and TEATER are CC0: a EuroLLM run with a
    glossary lists only permissive terms, and a LINDAT run lists LINDAT's and UDPipe's own."""
    declared = {c["name"]: c["license"] for c in _load_para_config(str(REPO_ROOT))["components"]}
    assert declared["amcr_vocab"] == declared["teater_data"] == "CC0"

    eurollm = _terms(CT2Translator(model_dir="/x", family="eurollm").license_components(True))
    assert eurollm == {"ctranslate2": "MIT", "eurollm": "Apache-2.0", "amcr_vocab": "CC0", "teater_data": "CC0"}

    from processors.translator import LindatTranslator

    lindat = _terms(LindatTranslator.license_components(object(), True))
    assert lindat["lindat_cubbitt"] == "CC BY-NC-SA 4.0"
    assert lindat["amcr_vocab"] == "CC0"


def test_supported_languages_from_kwarg_and_env(monkeypatch):
    assert CT2Translator(model_dir="/x", languages=["cs", "en"]).supported_languages() == ["cs", "en"]
    monkeypatch.setenv("CT2_LANGUAGES", "de, fr")
    assert CT2Translator(model_dir="/x").supported_languages() == ["de", "fr"]


def test_ctranslate2_missing_is_reported_clearly():
    """When ctranslate2 is absent, translate() raises a clear install hint
    rather than a bare ImportError (only meaningful if ctranslate2 is not
    installed in the test env)."""
    pytest.importorskip
    b = CT2Translator(model_dir="/definitely/not/a/real/model", family="eurollm")
    try:
        import ctranslate2  # noqa: F401
    except ImportError:
        with pytest.raises(TranslationError):
            b.translate("Ahoj světe dnes", "cs", "en")


# ── compute type ─────────────────────────────────────────────────────────────


def test_default_compute_type_is_int8(monkeypatch):
    """int4 was the default, and CTranslate2 has no int4: every run that did not
    set CT2_COMPUTE_TYPE failed on the first model load."""
    monkeypatch.delenv("CT2_COMPUTE_TYPE", raising=False)
    assert CT2Translator(model_dir="/x").compute_type == "int8"


def _fake_ctranslate2(supported=("float32", "int16", "int8", "int8_float32"), engine_error=None, device_error=None):
    """A stand-in `ctranslate2` module recording engine construction."""
    mod = types.ModuleType("ctranslate2")
    mod.constructed = []

    def get_supported_compute_types(device, device_index=0):
        if device_error is not None:
            raise device_error
        return set(supported)

    class _Engine:
        def __init__(self, model_dir, device="cpu", compute_type="default"):
            if engine_error is not None:
                raise engine_error
            mod.constructed.append((type(self).__name__, model_dir, device, compute_type))

    mod.get_supported_compute_types = get_supported_compute_types
    mod.Generator = type("Generator", (_Engine,), {})
    mod.Translator = type("Translator", (_Engine,), {})
    return mod


def test_unsupported_compute_type_names_the_valid_ones(monkeypatch):
    fake = _fake_ctranslate2()
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    b = CT2Translator(model_dir="/models/eurollm", family="eurollm", compute_type="int4")

    with pytest.raises(TranslationError) as excinfo:
        b.translate("Ahoj světe dnes", "cs", "en")

    message = str(excinfo.value)
    assert "CT2_COMPUTE_TYPE='int4'" in message
    for valid in ("float32", "int16", "int8", "int8_float32", "default", "auto"):
        assert valid in message
    assert fake.constructed == [], "the engine must not be built with a rejected type"


def test_supported_compute_type_builds_the_family_engine(monkeypatch):
    fake = _fake_ctranslate2()
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    b = CT2Translator(model_dir="/models/eurollm", family="eurollm", compute_type="int8")
    b._ensure_loaded()
    assert fake.constructed == [("Generator", "/models/eurollm", "cpu", "int8")]


@pytest.mark.parametrize("compute_type", ["default", "auto"])
def test_portable_compute_types_are_always_accepted(monkeypatch, compute_type):
    fake = _fake_ctranslate2(supported=("float32",))
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    CT2Translator(model_dir="/m", compute_type=compute_type)._ensure_loaded()
    assert fake.constructed[0][3] == compute_type


def test_unusable_device_is_a_translation_error(monkeypatch):
    fake = _fake_ctranslate2(device_error=RuntimeError("CUDA driver version is insufficient"))
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    b = CT2Translator(model_dir="/m", device="cuda")
    with pytest.raises(TranslationError, match="CT2_DEVICE='cuda' is not usable"):
        b._ensure_loaded()


def test_engine_value_error_is_wrapped(monkeypatch):
    fake = _fake_ctranslate2(engine_error=ValueError("Invalid compute type: int8"))
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    b = CT2Translator(model_dir="/m", compute_type="int8")
    with pytest.raises(TranslationError, match="CTranslate2 rejected"):
        b._ensure_loaded()


def test_real_ctranslate2_rejects_int4_through_the_backend(tmp_path):
    """Against the real library when it is installed (requirements-ct2.txt):
    the backend's own error fires before CTranslate2's bare ValueError."""
    pytest.importorskip("ctranslate2")
    b = CT2Translator(model_dir=str(tmp_path), family="eurollm", compute_type="int4")
    with pytest.raises(TranslationError, match="valid values: .*int8"):
        b.translate("Ahoj světe dnes", "cs", "en")


# ── model family → licence component ─────────────────────────────────────────


def test_unknown_family_raises_at_load():
    """Checked before importing ctranslate2, so the config error wins."""
    b = CT2Translator(model_dir="/x", family="marian")
    with pytest.raises(TranslationError, match="Unknown CT2_MODEL_FAMILY 'marian'"):
        b.translate("Ahoj světe dnes", "cs", "en")


def test_nllb_and_opus_have_their_own_components():
    assert CT2Translator(model_dir="/x", family="nllb").license_components(False) == ["ctranslate2", "nllb200"]
    assert CT2Translator(model_dir="/x", family="opus").license_components(False) == ["ctranslate2", "opus_mt"]


def test_every_family_component_is_declared_in_para_config():
    """An undeclared component is logged as "UNKNOWN" by atrium_paradata —
    which is how an NLLB run used to claim no licence at all."""
    declared = {c["name"]: c["license"] for c in _load_para_config(str(REPO_ROOT))["components"]}
    for family, component in ct2_translator._FAMILY_COMPONENTS.items():
        assert component in declared, f"{family!r} -> {component!r} is not in para_config.txt"
    assert "ctranslate2" in declared


def test_an_nllb_run_lists_its_non_commercial_model():
    comps = CT2Translator(model_dir="/x", family="nllb").license_components(False)
    assert _terms(comps) == {"ctranslate2": "MIT", "nllb200": "CC BY-NC 4.0"}


def test_an_opus_run_lists_the_attribution_bearing_model_licence():
    """The model's CC BY 4.0 is listed beside the MIT engine, each as declared."""
    comps = CT2Translator(model_dir="/x", family="opus").license_components(False)
    assert _terms(comps) == {"ctranslate2": "MIT", "opus_mt": "CC BY 4.0"}
