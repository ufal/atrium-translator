# Decision: replace or append (issue #46)

For anyone who asks why the translator overwrites the source text by default, and when `append` is the right
choice. Dated decisions of issue [#46](https://github.com/ufal/atrium-translator/issues/46) ("Experiments with
translator tool in production + functionality (replace-append question)"). How to use either mode:
[Choose the output mode](../usage.md#choose-the-output-mode-replace-or-append).

## 2026-09: a switch, not a verdict

Issue #46 asked two things at once: run the tool in AMČR's production, and decide whether the English should
**replace** the Czech or be **appended** beside it. The design answer was a switch rather than a verdict,
`--output-mode replace|append` (also `output_mode` in config.txt, `OUTPUT_MODE` and a `/translate` field), with
`replace` as the default, so that a real corpus, not an argument, would decide. `replace` output stayed
byte-identical across all 16 shipped samples (v1.1.0-beta).

`append` follows AMČR's own multilingual convention rather than inventing one: an AMČR thesaurus record stores a
concept as `<amcr:heslo xml:lang="cs">` beside `<amcr:heslo_en>` under one `@id`. Because the pair is detectable, a
second pass over appended output is a no-op rather than a double translation.

## ALTO: from a block label to an `ALTERNATIVE` per word box

The word-to-box correspondence in translated ALTO is constructed by the alignment, not observed
([ALTO dual-pass reconstruction](../how-it-works.md#alto-dual-pass-reconstruction)). That shaped the first ALTO
`append`: an English alternative per `String` would not be an alternative reading of that word, only whichever token
the bucketing landed there, so v1.1.0-beta labelled the block instead, and overwrote `CONTENT`, which lost the
scanned text. v1.2.0-beta reversed that: the scanned `CONTENT` stays, and each `String` gains an
`<ALTERNATIVE PURPOSE="translation:<lang>">` saying which English landed on that box, the same value `replace` writes,
read the same way, line by line. ALTO 3.1 declares `ALTERNATIVE` with a free-text `PURPOSE`, so the output stays
schema-valid.

## 2026-09-26 (v1.2.1-beta): AMČR 2.2 answers the question

A production-readiness review before the run found that `--xsd` could not load the AMČR schema at all (its import of
the W3C XML-namespace schema over `http://`, which libxml2 cannot fetch); v1.2.1-beta resolves the imports itself and
validates the record inside an OAI-PMH envelope. With `--xsd` working, the schema answered the question the switch
had left open: AMČR 2.2 accepts the 15 shipped records as source and as `replace` output (15/15), and rejects them as
`append` output (0/15): `xml:lang` is not declared on the free-text fields, and the repeated element is not allowed.
ALTO in either mode is valid ALTO. A run that appends to AMČR records has logged one warning since then.

## Production: `replace`

`replace` is AMČR's production mode. AMČR keeps the English in its separate `metadata-en` file, so overwriting the
Czech in the translated copy loses nothing; the Czech also stays in the `_log.csv`. AMČR's recipe is the batch image
with `--xpaths amcr-fields.txt --formats xml --source_lang cs --target_lang en --output-mode replace --xsd
https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd`, with `--xsd-strict` where an invalid record should fail the run; it
uses no vocabulary, since the vocabulary fields carry AMČR's own English labels. The shipped
`data_samples/in-place_translated_files/xml/` is a run of that shape (2026-10-19, with the curated vocabulary). ALTO
is not in the pilot.

`append` stays available for consumers that read the pair without validating against AMČR 2.2, and for ALTO, where
it is valid. Issue #46 is to close once AMČR's metadata run is recorded as an acceptance test (atrium-project#77, in
the hub's runbook since 2026-10-22); LINDAT's reliability moved to atrium-project#78.
