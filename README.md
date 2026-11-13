# ATRIUM translator: XML documents translated in place, ALTO pages and AMČR metadata

The translator translates the text inside an XML document and writes it back into the same document: every tag,
namespace and attribute stays, and in an ALTO page every word box keeps its coordinates. It reads ALTO pages (the
OCR text of scanned documents) and the free-text fields of AMČR metadata records, mainly Czech to English. Use it when
the result must stay the same document in another language. It reads XML only, not plain text or PDF.

[Releases](https://github.com/ufal/atrium-translator/releases) · [MIT licence](LICENSE) ·
[ATRIUM documentation](https://ufal.github.io/atrium-project/workflows/translator/) ·
[SSH Open Marketplace](https://marketplace.sshopencloud.eu/tool-or-service/CizIUW) ("Translator of XML files - ATRIUM")

## Inputs and outputs

| Input                                                                                        | Output                                                                                                  |
|----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| ALTO XML pages (an `<alto>` root, or `--alto`)                                               | the same pages, each word box's text translated, or given an `<ALTERNATIVE>` with the translation       |
| any other XML, with a list of XPaths (`--xpaths`): AMČR records, OAI-PMH responses, TEI      | the same documents, the named fields translated, or followed by an `xml:lang`-marked translated sibling |
| a file, a folder, or a `.txt` list of URLs (such as OAI-PMH `GetRecord` requests)            | per document, `<name>_log.csv`: the source and the translation side by side, with a `status` each       |
| optionally, an ATRIUM document record, and a vocabulary of Czech–English terms               | per document, the record handed back with nothing added; per run, the paradata                          |

Metadata mode translates the fields that a list of XPaths names, in AMČR records (bare or inside OAI-PMH envelopes) or
in any other XML. The pilot use is Czech to English: the free-text fields of AMČR metadata records, translated in
`replace` mode and checked against the AMČR 2.2 schema. The field list comes with each job (`--xpaths`, or the
service's `xpaths` field): AMČR passes its own list ([aiscr-webamcr#3570](https://github.com/ARUP-CAS/aiscr-webamcr/issues/3570)),
and [`amcr-fields.txt`](amcr-fields.txt) is only an example of one. ALTO mode, which translates the full text of OCR
pages, stays for research and batch use; it is not part of the pipeline. The target
language is English by default; any other language the backend offers can be chosen.

Two output modes: `replace`, the default, overwrites the source text; `append` keeps it and adds the translation
beside it. Under the hub's [document contract](https://ufal.github.io/atrium-project/ecosystem/document-contract/)
(record schema 2.0) the translator writes nothing into the document record: a record sent with a file is handed
back unchanged. Every file a run writes:
[docs/reference/outputs.md](docs/reference/outputs.md).

## Quick start

**What you need:** Docker, or Python 3.11 and a checkout. Network access to LINDAT (`lindat.mff.cuni.cz`: the
translation service, and UDPipe when a vocabulary is loaded) and, when `--xsd` names AMČR's schema as below, to
`api.aiscr.cz`. With `--source_lang auto`, the language-identification model: the images carry it, and a checkout
downloads it once from Hugging Face. No GPU: the translation is done by a remote service, or by a server you run.

The production shape of AMČR's run, on the 15 AMČR records in `data_samples/my_documents`, from a checkout with
Python 3.11:

```bash
git clone https://github.com/ufal/atrium-translator.git && cd atrium-translator
pip install -r requirements.txt
python main.py data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang cs --target_lang en --output-mode replace \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd -o out
```

The same run with the published batch image, which carries the records:

```bash
mkdir -p out
docker run --rm --user "$(id -u):0" -v "$PWD/out:/data/output" ghcr.io/ufal/atrium-translator:<version> \
    data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang cs --target_lang en --output-mode replace \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd -o /data/output    # <version>: the release without its v
```

Expected: exit code 0, and the log ends with `XSD: 15/15 record(s) valid.` For each record, `out/` holds
`<doc>_en.xml` with the free-text fields in English, `<doc>.document.json`, and `<doc>_log.csv`, one row per
translated field. A row looks like this (an invented record, for illustration):

```text
file,page_num,line_num,text_cs,text_en,status
C-TX-000000000,,//amcr:amcr/amcr:dokument/amcr:popis,"Záchranný výzkum při stavbě rodinného domu.","Rescue excavation during the construction of a family house.",ok
```

`out/paradata/` holds the run's record. The ALTO page in the same folder is skipped, because this is a metadata
run. The output of such a run, made on 2026-10-19, is in
[data_samples/in-place_translated_files/xml/](data_samples/in-place_translated_files/xml/) for comparison.

**What the run calls.** LINDAT's translation service, and its UDPipe service too, because the shipped
[config.txt](config.txt) names the bundled vocabulary (terms are matched by lemma); `api.aiscr.cz` for the schema.
Without LINDAT, each request is retried (`LINDAT_MAX_RETRIES`, 4 by default), then the document fails, the batch
goes on, and the run exits 3. Without UDPipe, the terms of a passage go unprotected and the run says so. Without
the schema, the run stops before translating anything, with exit code 1.

**As a service**, the `ghcr.io/ufal/atrium-translator-api` image answers `POST /translate` with the translated
document. It reads the same inputs the same way: the root element decides ALTO or metadata unless `is_alto` is
sent, and on request it returns the record (`include_record`), the log (`include_log`) and an XSD verdict
(`XSD_PATH`). What stays different, and why: [docs/usage.md](docs/usage.md#the-command-line-and-the-service).
Its start, endpoints and errors are in [service/README.md](service/README.md).

## How it works

The default backend is the [LINDAT translation service](https://lindat.mff.cuni.cz/services/translation/) (CUBBITT).
Two alternatives can be selected: a language model behind an OpenAI-compatible API (the recommended self-hosted one is
Qwen3.8-27B), and a self-hosted CTranslate2 model (`ct2`). Each document is translated in pieces: an ALTO text block,
or a metadata field. Unless the source language is
given, FastText identifies it per piece, and a guess is used only when it is confident and names a language the
backend can translate. Long texts are split at sentence boundaries. When a vocabulary is loaded, its terms keep
their agreed English: the LINDAT backend masks them before translation and restores them afterwards; the language
models get them as a glossary. Every reply is checked against its source. A reply that is empty, runaway, cut
short or looping is requested again, re-run at the end of the document, and, if it never recovers, the source text
is kept and logged `untranslated`. An ALTO block is translated whole for fluency and line by line as a ruler; the
fluent translation is then divided among the original lines and word boxes. Details:
[docs/how-it-works.md](docs/how-it-works.md).

## Where it fits in ATRIUM

In the AMČR pilot the translator is a record-level job, run on archiving: it turns an AMČR record's `metadata`
distribution into `metadata-en`, with the field list AMČR sends (atrium-project#84, D34). It is not a step of the
document pipeline, and it writes nothing into the document record. Until atrium-project#78 is settled, a
`metadata-en` produced through LINDAT is not published by AMČR (D35). The whole
workflow: the [hub's workflow page](https://ufal.github.io/atrium-project/workflows/translator/).

## Limits and maturity

- **A language pair the backend cannot translate is refused.** Nothing is translated first: the command line exits 1,
  and the service answers 422 `unsupported_language_pair`. Into English, LINDAT translates from Czech, German, French,
  Polish, Russian and Ukrainian ([how a pair is checked](docs/how-it-works.md#the-three-backends)).
- **LINDAT is a public research service without a service-level agreement.** One of its replicas once answered every
  other request with a loop. The output guard recovers such replies at the cost of time, and a daily check watches the
  endpoint ([what was found](docs/evaluation/lindat-replies.md)).
- **`append` output is not valid AMČR 2.2.** The schema allows neither `xml:lang` on the free-text fields nor a second
  copy of them. Where a record must validate, use `replace`, the default. ALTO output is valid ALTO 3.1 in either mode
  ([the decision](docs/decisions/output-mode.md)).
- **Word positions in translated ALTO are approximate.** Line boundaries are exact; how a line's words are spread over
  its word boxes is constructed ([how the words are placed](docs/how-it-works.md#alto-dual-pass-reconstruction)).
- **`ct2` on a GPU needs CUDA 12's cuBLAS** (12.8 or newer on a Blackwell card). EuroLLM on `ct2` drops short segments,
  and the published images do not include the `ct2` dependencies
  ([what was measured](docs/evaluation/backends.md#the-result-2026-10-19)).
- **Translation quality is not measured against references.** The backend comparison of 2026-10-19 is reference-less
  and small, and no human has scored the translations yet ([the bake-off](docs/evaluation/backends.md)). The tests
  check behaviour, not translation quality.
- **The service has no authentication or rate limiting.** Deploy it behind a gateway or on a trusted network
  ([known limits](service/README.md#known-limits)).

More: the service's [Known limits](service/README.md#known-limits).

## Documentation

| For                 | To                                                          | Read                                                                                                                                                   |
|---------------------|-------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| using it            | try it on the sample records                                | [Quick start](#quick-start)                                                                                                                            |
|                     | set it up, translate ALTO and metadata, choose a mode       | [docs/usage.md](docs/usage.md)                                                                                                                         |
|                     | look up the command line, outputs, paradata, the vocabulary | [cli](docs/reference/cli.md), [outputs](docs/reference/outputs.md), [paradata](docs/reference/paradata.md), [vocabulary](docs/reference/vocabulary.md) |
|                     | set every option: config.txt and the environment            | [docs/configuration.md](docs/configuration.md)                                                                                                         |
| running the service | deploy and operate it, its errors and limits                | [service/README.md](service/README.md)                                                                                                                 |
|                     | see the API contract                                        | [service/openapi.json](service/openapi.json)                                                                                                           |
| judging the results | see the limits before relying on the output                 | [Limits and maturity](#limits-and-maturity)                                                                                                            |
|                     | see what was measured: the backends, LINDAT's replies       | [backends](docs/evaluation/backends.md), [LINDAT's replies](docs/evaluation/lindat-replies.md)                                                         |
|                     | see the components of a run and their licences              | [docs/reference/licences.md](docs/reference/licences.md)                                                                                               |
| changing the code   | understand languages, the guard, the ALTO reconstruction    | [docs/how-it-works.md](docs/how-it-works.md)                                                                                                           |
|                     | read why it is built this way                               | [backend choice](docs/decisions/backend-choice.md), [replace or append](docs/decisions/output-mode.md), [history](docs/decisions/history.md)           |
|                     | contribute, test, release; the release history              | [docs/development.md](docs/development.md), [CONTRIBUTING.md](CONTRIBUTING.md)                                                                         |

Terms used across the tools are explained in the [ATRIUM glossary](https://ufal.github.io/atrium-project/glossary/).
The documentation follows the ATRIUM
[documentation style](https://github.com/ufal/atrium-project/blob/main/docs/documentation-style.md).

## Licence, citation and acknowledgements

The code is MIT ([LICENSE](LICENSE)). Each run records the components it used, each with its own licence or
terms. The LINDAT translation and UDPipe services are offered under the
[LINDAT Terms of Use](https://lindat.mff.cuni.cz/en/terms-of-use), and their CUBBITT and UDPipe models are
CC BY-NC-SA 4.0. The [FastText language-identification model](https://huggingface.co/facebook/fasttext-language-identification)
is CC BY-NC 4.0 and is used only when the source language is detected. The AMČR and TEATER vocabularies are CC0. A
self-hosted model has its own licence; Qwen3.8-27B, for example, is Apache-2.0. None of these is the licence of
the translation. The rights in an output depend on the source document, and are stated by whoever holds them ([Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/)).
The components, when each is used, and the inventory in [para_config.txt](para_config.txt):
[docs/reference/licences.md](docs/reference/licences.md). Cite with [CITATION.cff](CITATION.cff), which GitHub shows as
"Cite this repository".

Developed by [ÚFAL](https://ufal.mff.cuni.cz/), Charles University, with AMČR (the Archaeological Map of the Czech
Republic) as the pipeline partner. Funded by [ATRIUM](https://atrium-research.eu/) (GA 101132163), with the support
of the LINDAT/CLARIAH-CZ research infrastructure and the AIS CR infrastructure (LM2023031). It translates with the
[LINDAT translation service](https://lindat.mff.cuni.cz/services/translation/), lemmatises with
[UDPipe](https://lindat.mff.cuni.cz/services/udpipe/), and builds its vocabulary from the
[AMČR OAI-PMH API](https://api.aiscr.cz/2.2/oai?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo) and
[TEATER](https://teater.aiscr.cz/). © 2026 ÚFAL and ATRIUM. Contact: the
[issue tracker](https://github.com/ufal/atrium-translator/issues), or lutsai@ufal.mff.cuni.cz.
