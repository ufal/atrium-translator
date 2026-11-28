## Sample outputs

> The two AMCR replace folders (`in-place_translated_files/xml`, `ct2_translated_files/xml`) are made with this
> version and carry its paradata 3.0 in `paradata/`; a run writes no record of its own and lists components as facts.
> The ALTO and append folders are older research samples: their translated documents and QA logs stay, without
> paradata (git history up to `v1.7.8-beta` keeps their records and paradata, which predate record schema 2.0).
> ALTO translation is research and batch use, not a step of the AMČR pipeline, whose job is the record-level
> `metadata` → `metadata-en` translation with the field list AMČR sends.

The same inputs (`my_documents/`) translated in both output modes of issue #46, one folder per mode and per
input kind, and the AMCR records once more through the self-hosted `ct2` backend (issue #4), in a folder of their own:

```
data_samples/
├── my_documents/                 inputs: 15 AMCR metadata records + MTX201501307_anon.alto.xml
├── vocabulary.csv                the glossary (keyword-extract's curated list, see below)
├── in-place_translated_files/    --output-mode replace (the default)
│   ├── alto/                     the ALTO sample            (lindat; research, no paradata)
│   └── xml/                      the AMCR records           (lindat; this version, with paradata/)
├── appended_translated_files/    --output-mode append
│   ├── alto/                                                (lindat; research, no paradata)
│   └── xml/                                                 (lindat; research, no paradata)
└── ct2_translated_files/         --output-mode replace, self-hosted
    └── xml/                      the AMCR records           (ct2, EuroLLM-1.7B; this version, with paradata/)
```

Which run produced each folder:

| Folder                           | Backend                                  | `--source_lang` | `--xsd`  | Run                                        |
|----------------------------------|------------------------------------------|-----------------|----------|--------------------------------------------|
| `in-place_translated_files/alto` | `lindat` (LINDAT CUBBITT)                | `auto`          | —        | `260926-122251`, in git history            |
| `appended_translated_files/alto` | `lindat`                                 | `auto`          | —        | `260926-120736`, in git history            |
| `in-place_translated_files/xml`  | `lindat`                                 | `cs`            | AMCR 2.2 | this version; its run id names `paradata/` |
| `appended_translated_files/xml`  | `lindat`                                 | `auto`          | —        | `260926-140310`, in git history            |
| `ct2_translated_files/xml`       | `ct2` (EuroLLM-1.7B-Instruct, int8, GPU) | `cs`            | AMCR 2.2 | this version; its run id names `paradata/` |

> **The AMCR replace folder is the shape of AMČR's production run** (issue #46): LINDAT, `replace`,
> `--source_lang cs`, the curated vocabulary, validated against AMCR 2.2 with `--xsd`, and the field list of
> [amcr-fields.example.txt](../amcr-fields.example.txt), an example: AMČR sends its own list with each job. Its
> paradata record the field list (`parameters.xpaths`), the vocabulary terms protected and the degenerate LINDAT
> replies that were re-requested; each field's `status` is in the document's `_log.csv`.
>
> **`ct2_translated_files/xml` is the `ct2` EuroLLM-1.7B run of the same job**, made with this version on a GPU (the
> paradata name the compute type). Every field is translated: since 2.0.0-beta a field whose reply the guard refuses
> fails the job. EuroLLM-1.7B ran on past short fields and once wrote only the prompt's `Czech: …` line, until a
> one-line source was stopped at the end of its line and such a reply continued from that line
> ([the EuroLLM notes](../docs/evaluation/backends.md#eurollm-utter-project)). The bake-off found EuroLLM unfit as the
> self-hosted alternative ([the backend decision](../docs/decisions/backend-choice.md)); the folder stays as the
> record of that backend.

Each folder holds, per input document:

| File                                 | Content                                                                                                                     |
|--------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| `<doc>_en.alto.xml` / `<doc>_en.xml` | the translated document                                                                                                     |
| `<doc>_log.csv`                      | QA log: `file, page_num, line_num, text_<src>, text_en, status` — one row per ALTO line / metadata field, in document order |

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

> The AMCR/TEATER vocabulary is **CC0**, as its rights holder AMČR stated
> ([atrium-project#6](https://github.com/ufal/atrium-project/issues/6#issuecomment-5867861653), 2026-09-28). A LINDAT
> run lists LINDAT and the UDPipe models, CC BY-NC-SA 4.0. A `ct2` EuroLLM run with the vocabulary and `--source_lang cs` lists only permissive components and
> the CC0 vocabulary.
>
> **`vocabulary.csv`** is **CC0** per that statement. Since 2026-10-18 it is built from atrium-keyword-extract's
> `v1.3.0-beta` vocabulary (`python load_vocab.py --from-release v1.3.0-beta`): the 4,715 pairs of its curated list, each
> with the identity of the concept that won it and, for 1,961 of them, the concept's description (cut to 500 characters).
> The harvest of September 2026 it replaces had 5,087 pairs. The two AMCR replace folders are made
> with this list; the ALTO and append folders were made with the older one. The other files here stay under
> [LICENSE](LICENSE) (CC BY-NC 4.0).

### Regenerating them

Run from the repository root, **one command at a time** — concurrent runs against the public LINDAT endpoint
coincided with the degenerate replies that motivated the guard — and commit a folder only after its run has printed
`PROCESSING COMPLETE`: each document's log and XML are replaced only when that document is finished. Each run writes
its paradata into the folder's `paradata/`; commit them with the folder, as the run wrote them (the whitespace hooks
leave the sample folders alone, so the SHA-512s in the paradata still match; keep the logs' CRLF line ends, as the
run wrote them, for the same reason). The paradata `config` holds the run's settings only: the records are its
`metadata` inputs and `metadata-en` outputs, and the vocabulary, the field list, a local XSD and each `_log.csv` are
entries by file name and SHA-512, never by the path the command line was given (atrium-project#84).

```bash
# AMCR metadata, LINDAT, replace: the production shape of issue #46
python main.py data_samples/my_documents --xpaths amcr-fields.example.txt --formats xml \
    --source_lang cs --vocabulary data_samples/vocabulary.csv \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd \
    --output data_samples/in-place_translated_files/xml --output-mode replace
```

The same records through the self-hosted `ct2` EuroLLM backend (issue #4; install `requirements-ct2.txt`, convert the
model as in [the EuroLLM notes](../docs/evaluation/backends.md#eurollm-utter-project)) — into a folder of its own, so the
LINDAT set stays as it is:

```bash
# On a GPU: CT2_DEVICE=cuda CT2_COMPUTE_TYPE=int8_float16 (a Pascal card has int8_float32 only); on a CPU: cpu, int8
export CT2_MODEL_DIR=models/ct2/eurollm-1.7b-int8 CT2_MODEL_FAMILY=eurollm \
       CT2_TOKENIZER_DIR=models/hf/EuroLLM-1.7B-Instruct \
       CT2_DEVICE=cuda CT2_COMPUTE_TYPE=int8_float16 CT2_LANGUAGES=cs,en
python main.py data_samples/my_documents --xpaths amcr-fields.example.txt --formats xml \
    --backend ct2 --source_lang cs --vocabulary data_samples/vocabulary.csv \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd \
    --output data_samples/ct2_translated_files/xml --output-mode replace
```

The research folders (ALTO, append) are made the same way with `--alto` or `--output-mode append`; their commands are
in git history up to `v1.7.8-beta`.

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
