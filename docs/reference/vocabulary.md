# The vocabulary file

For anyone who edits the vocabulary or writes one for another language. It answers what the vocabulary CSV must
contain and which lemma form each source language needs. How the vocabulary is applied:
[Vocabulary protection](../how-it-works.md#vocabulary-protection); how it is built:
[Build the vocabulary](../usage.md#build-the-vocabulary).

## The CSV format

A UTF-8 CSV whose first two columns are the term pair:

```text
source_lemma,target_translation
kostel,church
pohřebiště,burial ground
fotografie události,photograph of event
```

Columns 3–5 are **optional provenance** and are ignored by the translator; column 6, `description`, is read only when
a header names it, and only the `openai_compatible` backend uses it. Both the two-column and the longer form load.
The shipped [vocabulary.csv](../../data_samples/vocabulary.csv) carries all six, so every term can be traced to its
thesaurus concept:

```text
source_lemma,target_translation,source,source_id,uri,description
archeolog,archaeologist,teater,4,https://teater.aiscr.cz/id/4,
```

| Column                   | Content                                                                                                                                                                                                                          |
|--------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `source_lemma`           | The **lemma (dictionary form)** of the source term. For a single word it must match what UDPipe returns for the source language (below). A multi-word phrase is matched on the surface form given here.                          |
| `target_translation`     | The agreed translation, usually the preferred English term of a controlled vocabulary or thesaurus.                                                                                                                              |
| `source` (optional)      | Which thesaurus the pair came from: `amcr` or `teater`.                                                                                                                                                                          |
| `source_id` (optional)   | The concept's id in that thesaurus: the AMČR `heslo` id (`HES-…`) or the TEATER concept id.                                                                                                                                      |
| `uri` (optional)         | The dereferenceable concept URI built from `source_id` (`https://api.aiscr.cz/id/…` or `https://teater.aiscr.cz/id/…`).                                                                                                          |
| `description` (optional) | The concept's scope note (`note_en`, else `note_cs`), cut to 500 characters. Read **by header name only**. Given to the LLM backend as context when `LLM_GLOSSARY_DESCRIPTION_CHARS` > 0 (issue #4); LINDAT and `ct2` ignore it. |

Single-word entries should be lemmas (the singular, the infinitive): matching is by lemma and is number-neutral.

**The shipped list** (since 2026-10-18) is built from atrium-keyword-extract's release asset
(`python load_vocab.py --from-release v1.3.0-beta`): the 4,715 pairs of its curated `vocabulary.csv`, so the project
curates one list rather than two (AMČR, 13 October 2026). Each pair keeps its concept identity (`source`,
`source_id`, `uri`), and 1,961 carry the concept's description. Most descriptions are Czech: TEATER has them for
about half its terms, AMČR for few. AMČR asked for the LLM backend to translate "with the vocab, including
descriptions, as one of the inputs"; `LLM_GLOSSARY_DESCRIPTION_CHARS` gives a matched term's description to the
model beside the pair, and a longer description is cut at a word (a `trimmed` note). It stays off by default
([the decision](../decisions/backend-choice.md)). Only `openai_compatible` uses descriptions: EuroLLM-1.7B (`ct2`)
already echoed the bare glossary into its output before the prompt fix of 2026-09-28, and its input budget is small.

## The lemma form by language

`source_lemma` must match the form UDPipe gives as the lemma for the source language. To check a word, run it
through the [online UDPipe demo](https://lindat.mff.cuni.cz/services/udpipe/) and read the `LEMMA` column of the
CoNLL-U output.

| Source language (`--source_lang`) | UDPipe model                | Lemma convention                                    | Example                                         |
|-----------------------------------|-----------------------------|-----------------------------------------------------|-------------------------------------------------|
| Czech `cs`                        | `czech-pdt-ud-2.15`         | Nominative singular for nouns; infinitive for verbs | `kostel` (not `kostela`), `kopat` (not `kopal`) |
| Slovak `sk`                       | `slovak-snk-ud-2.15`        | Nominative singular; infinitive                     | `kostol`, `kopať`                               |
| Polish `pl`                       | `polish-pdb-ud-2.15`        | Nominative singular; infinitive                     | `kościół`, `kopać`                              |
| German `de`                       | `german-gsd-ud-2.15`        | Nominative singular; infinitive                     | `Kirche`, `graben`                              |
| French `fr`                       | `french-gsd-ud-2.15`        | Nominative singular; infinitive                     | `église`, `fouiller`                            |
| Russian `ru`                      | `russian-syntagrus-ud-2.15` | Nominative singular; infinitive                     | `церковь`, `копать`                             |
| Ukrainian `uk`                    | `ukrainian-iu-ud-2.15`      | Nominative singular; infinitive                     | `церква`, `копати`                              |
| English `en`                      | `english-ewt-ud-2.15`       | Base form                                           | `church`, `dig`                                 |

The models' full names carry the release date, `czech-pdt-ud-2.15-241121` and so on. Any other `--source_lang` has
**no UDPipe model**: the single-word lemma pass is skipped for it, with one `[WARN]` per language, instead of running
the text through the Czech model, whose lemmas would be noise that can still collide with a Czech vocabulary key.
Multi-word phrases are still protected.

**For an archive in another language** than Czech, pass its `--source_lang` and supply a vocabulary whose
`source_lemma` column follows that language's lemma conventions. [load_vocab.py](../../load_vocab.py) builds a Czech
vocabulary (AMČR and TEATER); for other languages, compile the vocabulary yourself or from your own thesaurus.
