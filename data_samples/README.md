## Sample outputs

The same inputs (`my_documents/`) translated in both output modes of issue #46, one folder per mode and per
input kind:

```
data_samples/
├── my_documents/                 inputs: 15 AMCR metadata records + MTX201501307_anon.alto.xml
├── in-place_translated_files/    --output-mode replace (the default)
│   ├── alto/                     the ALTO sample                         (lindat)
│   └── xml/                      the AMCR records                        (lindat)
└── appended_translated_files/    --output-mode append
    ├── alto/                                                             (lindat)
    └── xml/                                                              (lindat)
```

Which run produced each folder — each `paradata/` folder holds that run's record:

| Folder                           | Backend                                          | `--source_lang` | `--xsd`  | Run             | Log `status`             |
|----------------------------------|--------------------------------------------------|-----------------|----------|-----------------|--------------------------|
| `in-place_translated_files/alto` | `lindat` (LINDAT CUBBITT)                        | `auto`          | —        | `260926-122251` | 2095 `ok`                |
| `appended_translated_files/alto` | `lindat`                                         | `auto`          | —        | `260926-120736` | 2095 `ok`                |
| `in-place_translated_files/xml`  | `lindat`                                         | `cs`            | AMCR 2.2 | `260928-110452` | 37 `ok`                  |
| `appended_translated_files/xml`  | `lindat`                                         | `auto`          | —        | `260926-140310` | 37 `ok`                  |

> **The AMCR replace folder is the shape of AMČR's production run** (issue #46): LINDAT, `replace`,
> `--source_lang cs`, validated against AMCR 2.2 with `--xsd`, made with v1.2.2-beta on 2026-09-28. All 15 records
> were translated; the 37 degenerate LINDAT replies of the run were re-requested and recovered (`lindat_degenerate_replies`
> in its paradata), so every field is `ok`. The first `ct2` (EuroLLM-1.7B) run of these records, from 2026-09-27, is in
> git history at `1e1ce67`; it predates the EuroLLM prompt fix.

Each folder holds, per input document:

| File                                 | Content                                                                                                                     |
|--------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| `<doc>_en.alto.xml` / `<doc>_en.xml` | the translated document                                                                                                     |
| `<doc>_log.csv`                      | QA log: `file, page_num, line_num, text_<src>, text_en, status` — one row per ALTO line / metadata field, in document order |
| `<doc>.document.json`                | the ATRIUM Document record (`translations.output_mode`, `translations.detected_source_lang` with `source_lang=auto`)        |
| `paradata/<run>_translator.json`     | the run's provenance record (configuration incl. the language policy, licences, counts)                                     |

What the two modes look like:

* **replace, ALTO** — every `String/@CONTENT` holds the English words aligned to that box; an existing block
  `LANG` is moved to `en`.
* **append, ALTO** — every `CONTENT` is the scanned Czech, untouched; the English for that box is in
  `<ALTERNATIVE PURPOSE="translation:en">` inside the `String`.
* **replace, AMCR** — the targeted field's text is English.
* **append, AMCR** — the Czech field is kept (and gets `xml:lang="cs"`), followed by a sibling with the same tag
  and `xml:lang="en"`.

Against the published schemas: the ALTO source, replace and append files all validate against **ALTO 3.1**; the AMCR
records validate against **AMCR 2.2** as source (15/15) and as replace output (15/15), and **not** as append output
(0/15 — `xml:lang` is not declared on the free-text fields and the repeated element is not allowed there). Check a
folder with `--xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd` on a metadata run.

`status` in the log is `ok`, `rerun` (the backend's reply was degenerate and the end-of-document re-run
recovered it), `approx_alignment` (ALTO: the line's anchor was unusable, so its words were placed by word count)
or `untranslated` (still degenerate after the re-run — the source text was kept, the target cell is empty); with
`--source_lang auto` also `unsupported_lang` (identified with confidence as a language the backend cannot translate,
and translated from the fallback language; translator#52). A table
column whose block translation merged repeated cells (page 76 of the ALTO sample) is placed line by line, each cell
from its own translation, so no number moves to its neighbour's row.

> The `.document.json` of both ALTO samples and of the first AMCR record of the append folder state **CC BY-NC 4.0**
> (`fasttext` only): they were written before the backend's licence components were recorded, which later code
> fixes. Those runs' paradata and their other records state the run's actual licence, **CC BY-NC-SA 4.0**; the replace
> folder's records (v1.2.2-beta) all do. The next LINDAT refresh below rewrites the others. The AMCR/TEATER vocabulary
> is **CC0**, as its rights holder AMČR stated
> ([atrium-project#6](https://github.com/ufal/atrium-project/issues/6#issuecomment-5867861653), 2026-09-28); paradata
> written before v1.2.2-beta labels it CC BY-NC 4.0. With LINDAT that changes nothing: LINDAT and the UDPipe models are
> CC BY-NC-SA 4.0. A `ct2` EuroLLM run with the vocabulary and `--source_lang cs` resolves to **MIT**.
>
> **`vocabulary.csv`** (AMCR `heslo` + TEATER terms, harvested by `load_vocab.py`) is **CC0** per that statement. The
> other files here stay under [LICENSE](LICENSE) (CC BY-NC 4.0).

### Regenerating them

Run from the repository root, **one command at a time** — concurrent runs against the public LINDAT endpoint
coincided with the degenerate replies that motivated the guard — and commit a folder only after its run has printed
`PROCESSING COMPLETE`: each document's log, XML and record are replaced only when that document is finished. Remove
the old run records first so each `paradata/` folder holds the record of the run that produced its files:

```bash
rm -f data_samples/*/*/paradata/*.json

# ALTO
python main.py data_samples/my_documents/MTX201501307_anon.alto.xml --alto \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/in-place_translated_files/alto --output-mode replace
python main.py data_samples/my_documents/MTX201501307_anon.alto.xml --alto \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/appended_translated_files/alto --output-mode append

# AMCR metadata (a metadata run leaves *.alto.xml out of a directory scan) — LINDAT
# replace: the production shape of issue #46
python main.py data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang cs --vocabulary data_samples/vocabulary.csv \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd \
    --output data_samples/in-place_translated_files/xml --output-mode replace
python main.py data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/appended_translated_files/xml --output-mode append
```

The same records through the self-hosted `ct2` EuroLLM backend (issue #4; install `requirements-ct2.txt`, convert the
model as in [docs/translation-backends.md](../docs/translation-backends.md)) — into a folder of its own, so the
committed LINDAT set stays as it is:

```bash
export CT2_MODEL_DIR="$PWD/models/ct2/eurollm-1.7b-int8" CT2_MODEL_FAMILY=eurollm \
       CT2_TOKENIZER_DIR="$PWD/models/hf/EuroLLM-1.7B-Instruct" \
       CT2_DEVICE=cpu CT2_COMPUTE_TYPE=int8 CT2_LANGUAGES=cs,en
python main.py data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --backend ct2 --source_lang cs --vocabulary data_samples/vocabulary.csv \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd \
    --output /tmp/ct2_translated_files/xml --output-mode replace
```

## Sample inputs

### MTX201501307.alto.xml anonymized to be used as a translator test input:

Only `String CONTENT="..."` values were touched. All XML structure, namespaces, schema reference, ParagraphStyle IDs,
block/line/glyph geometry (HPOS/VPOS/HEIGHT/WIDTH), coordinates, and the survey-point dump at the end are byte-identical.
The BOM and CRLF line endings are preserved exactly, and the file still validates as ALTO v3 XML and has the same **5527** lines.

Grammar preserved via inflection-aware mapping. Czech declines names heavily, so each inflected surface form maps to
a matching invented form in the same case/paradigm — e.g. `Nového Města` → `Starého Sídla`, `Nedvědička`/`Nedvědičkou`
→ `Vrbice`/`Vrbicí`, `Heralt`/`Heraltovi`/`Heraltově` → `Načerat`/`Načeratovi`/`Načeratově`. Punctuation attached to
tokens (commas, periods, citation parens, quotes) is stripped, matched, and reattached, so bibliography entries and
parenthetical citations keep their shape.

#### Entities replaced (95 distinct surface forms):

- Village `Zubří` → `Vraní`, with the etymology line kept coherent (`"místo kde jsou zubři"` → `"místo kde jsou vrány"`,
both matching the new crow-derived name).

- Places/hydronyms: `Nové Město na Moravě`, `Praha-Chodov`, `Žďár nad Sázavou`, `Jihlava`, `Brno`,
`Bítešská vrchovina`, `Harusův kopec`, `Nedvědička`/`Divišovský potok`, `Olešná`, `Lažínek`, `Jevišovka`,
`Střelice`, `Loučka`, `Olešínky`, `Pohledec`, `Jimramov`, plus historical estates (`Bystřice`, `Pyšolec`,
`Kunštát`, `Pernštejn`, `Ditrichštejn`, `Boskovice`, etc.).

- Team members (`Baier`, `Kaiser`, `Bařinka`, `Švácha`, `Hrušková`, `Hoffmannová`, `Kossl`) and historical figures
(`Jimram`, `Jošt`, `Heralt`, the `Lucemburks`, etc.) all given invented equivalents.

- Deliberately left generic words that merely resemble entities (`moravský`, `bystřické`, `plynové`, `pánové`, etc.)
untouched.

- The company name `Pueblo – archeologická společnost` was left as-is since it's the
[report producer](https://www.pueblo-archaeology.org/home), not a personal or place name.
