## Sample outputs

The same inputs (`my_documents/`) translated in both output modes of issue #46, one folder per mode and per
input kind:

```
data_samples/
├── my_documents/                 inputs: 15 AMCR metadata records + MTX201501307_anon.alto.xml
├── in-place_translated_files/    --output-mode replace (the default)
│   ├── alto/                     the ALTO sample
│   └── xml/                      the AMCR records
└── appended_translated_files/    --output-mode append
    ├── alto/
    └── xml/
```

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

`status` in the log is `ok`, `rerun` (the backend's reply was degenerate and the end-of-document re-run
recovered it), `approx_alignment` (ALTO: the line's anchor was unusable, so its words were placed by word count)
or `untranslated` (still degenerate after the re-run — the source text was kept, the target cell is empty).

### Regenerating them

Run from the repository root, **one command at a time** — concurrent runs against the public LINDAT endpoint
coincided with the degenerate replies that motivated the guard. Remove the old run records first so each
`paradata/` folder holds the record of the run that produced its files:

```bash
rm -f data_samples/*/*/paradata/*.json

# ALTO
python main.py data_samples/my_documents/MTX201501307_anon.alto.xml --alto \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/in-place_translated_files/alto --output-mode replace
python main.py data_samples/my_documents/MTX201501307_anon.alto.xml --alto \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/appended_translated_files/alto --output-mode append

# AMCR metadata (a metadata run leaves *.alto.xml out of a directory scan)
python main.py data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/in-place_translated_files/xml --output-mode replace
python main.py data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang auto --vocabulary data_samples/vocabulary.csv \
    --output data_samples/appended_translated_files/xml --output-mode append
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
