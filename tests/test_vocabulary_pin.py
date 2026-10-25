"""tests/test_vocabulary_pin.py -- the bundled vocabulary is a pinned release asset (atrium-project#82).

atrium-keyword-extract owns the project's one vocabulary and publishes it as an immutable release
asset; this repository ships a CSV built from one such asset (`load_vocab.py --from-release`) in
`data_samples/vocabulary.csv`, which the image carries. That is a data dependency, not a code
copy, so it is pinned by release and checksums rather than by byte parity with the owner's tree:

* `release`: the keyword-extract release the CSV was built from;
* `asset_sha256`: the digest of that release's `atrium-vocabulary-<release>.zip`, as the release
  lists it (an immutable release, so the digest is fixed);
* `csv_sha256`: the digest of the bundled CSV (line endings normalised to ``\\n``).

A newer keyword-extract release is not a defect here: the translator adopts it deliberately, by
rebuilding the CSV and updating all three values and docs/usage.md in one change.
"""

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VOCABULARY_PIN = {
    "owner": "atrium-keyword-extract",
    "release": "v1.3.0-beta",
    "asset": "atrium-vocabulary-v1.3.0-beta.zip",
    "asset_sha256": "948d7d37c5f676a3c99b8ffe113259b367545a0de1fa3c563fb8f4dcf230954a",
    "csv": "data_samples/vocabulary.csv",
    "csv_sha256": "1d7af82172451215c085a8f86052743a4a58099817288c425f3fa80cf7f2ec39",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_the_bundled_csv_is_the_pinned_build():
    assert _digest(REPO_ROOT / VOCABULARY_PIN["csv"]) == VOCABULARY_PIN["csv_sha256"], (
        "data_samples/vocabulary.csv changed without its pin: rebuild it with "
        "`load_vocab.py --from-release <release>` and update VOCABULARY_PIN and docs/usage.md together"
    )


def test_the_pin_names_an_immutable_release_asset():
    assert re.fullmatch(r"v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", VOCABULARY_PIN["release"])
    assert VOCABULARY_PIN["asset"] == f"atrium-vocabulary-{VOCABULARY_PIN['release']}.zip"
    assert re.fullmatch(r"[0-9a-f]{64}", VOCABULARY_PIN["asset_sha256"])


def test_the_usage_guide_names_the_pinned_release():
    usage = (REPO_ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    assert f"The committed `vocabulary.csv` is built from `{VOCABULARY_PIN['release']}`" in usage, (
        "docs/usage.md names another vocabulary release than VOCABULARY_PIN"
    )
