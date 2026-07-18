<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11-blue.svg" title="Python Version"></a>
  <a href="https://lindat.mff.cuni.cz/services/translation/"><img src="https://img.shields.io/badge/API-LINDAT%20Translation-0055A4.svg" title="LINDAT Translation API"></a>
  <a href="https://huggingface.co/facebook/fasttext-language-identification"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20HF-fasttext--langID-yellow.svg" title="FastText Language Identification"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-translator" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---

# ATRIUM Translator - Agent Skill 🤖🌍

### Goal: let coding agents translate archival XML documents via a server-client skill

This branch (`agent-skill`) packages the **ATRIUM Translator API service** together
with a **Skill for coding agents** (Claude Code, Codex, Gemini/Antigravity). The
design follows a strict server-client split:

- **Server** 🖥️ - the FastAPI service in [`service/`](service/) drives the
  translation pipeline: LINDAT/CUBBITT NMT (or an LLM backend), FastText source
  detection, structure-preserving XML rewriting (Docker or local venv).
- **Client** 🪶 - [`scripts/atrium_translate.py`](scripts/atrium_translate.py), a
  **zero-dependency** stdlib-only script that agents call directly.
- **Skill contract** 📜 - [`SKILL.md`](SKILL.md) tells the agent when and how to
  use it: ALTO vs metadata modes, language handling, error playbooks.

For the batch CLI, vocabulary Tag-and-Protect tooling, backend evaluation, and full
project documentation, see the
[`test`](https://github.com/ufal/atrium-translator/tree/test) branch - this branch
intentionally carries only what the skill needs.

### Table of contents 📑

  * [Quick start 🚀](#quick-start-)
  * [Skill installation 🔧](#skill-installation-)
  * [Server setup 🖥️](#server-setup-)
  * [Client usage 🪶](#client-usage-)
  * [Remote server / LINDAT 🌐](#remote-server--lindat-)
  * [Maintenance notes 🔍](#maintenance-notes-)
  * [Contacts 📧](#contacts-)

----

## Quick start 🚀

```bash
git clone -b agent-skill https://github.com/ufal/atrium-translator.git
cd atrium-translator

bash scripts/server.sh                                                        # start the server
python3 scripts/atrium_translate.py small_data_samples/MTX201501307_anon.alto.xml
```

> [!NOTE]
> Translation runs against the remote LINDAT API - the server needs outbound
> network access at request time. Long documents take minutes. ⏳

## Skill installation 🔧

### Claude Code

```bash
git clone -b agent-skill https://github.com/ufal/atrium-translator.git \
    ~/.claude/skills/atrium-translator
```

Restart Claude Code - the skill is available as `/atrium-translator` and is selected
automatically for translation requests. For a project-local install, clone into
`.claude/skills/atrium-translator` inside the target repository.

### Codex

```bash
git clone -b agent-skill https://github.com/ufal/atrium-translator.git \
    ~/.codex/skills/atrium-translator
```

The skill is detected automatically in the next Codex session.

### Google Antigravity

Clone the branch into your project and point `AGENTS.md` at it:

```
Use the ATRIUM translator skill from `atrium-translator/SKILL.md` for
translating ALTO OCR pages and AMCR metadata XML between languages.
Start the server with `bash atrium-translator/scripts/server.sh`, then run
`python3 atrium-translator/scripts/atrium_translate.py [FILES...]`.
```

Update any install with `git pull` inside the cloned skill directory.

## Server setup 🖥️

The server exposes three endpoints (see [`service/README.md`](service/README.md)
for details): `GET /info`, `GET /health`, `POST /translate`. A minimal demo
frontend is mounted at `/frontend`.

```bash
bash scripts/server.sh          # auto: Docker Compose api service, else local uvicorn
bash scripts/server.sh --local  # force local uvicorn (no Docker)
```

The script is idempotent and health-waits on `/info`. Port defaults to `8000`
(`ATRIUM_TR_PORT` to change). Backend selection: `TRANSLATION_BACKEND=lindat`
(default) or `openai_compatible`.

## Client usage 🪶

```bash
python3 scripts/atrium_translate.py page.alto.xml                     # ALTO → page_en.alto.xml
python3 scripts/atrium_translate.py record.xml --no-alto              # AMCR metadata mode
python3 scripts/atrium_translate.py page.alto.xml --target-lang de    # different target
python3 scripts/atrium_translate.py page.alto.xml -o -                # XML to stdout
python3 scripts/atrium_translate.py --info                            # capabilities
```

The output is the translated XML document (structure preserved); mode and
language semantics are documented in [`SKILL.md`](SKILL.md#modes--languages-).

## Remote server / LINDAT 🌐

The client is location-agnostic: point it at any deployment with `--base-url` or

```bash
export ATRIUM_TR_URL="https://<hosted-instance>/atrium-tr"
```

A hosted LINDAT instance is planned; once available, the environment variable is the
only change needed - the skill contract and client stay identical.

## Maintenance notes 🔍

Review checklist for every change / sync-merge into this branch (the ATRIUM skill
anti-pattern checklist):

- [ ] no doc references a script name that differs from the committed file;
- [ ] no provenance/paradata claim unless the service imports it on this branch;
- [ ] no reference to directories/files absent from this branch;
- [ ] documented response fields match what `service/api.py` actually returns;
- [ ] client smoke test re-run on `small_data_samples/` against a locally started server.

## Contacts 📧

**For support write to:** lutsai.k@gmail.com responsible for the
[GitHub repository](https://github.com/ufal/atrium-translator)

### Acknowledgements 🙏

- **Developed by** UFAL, Charles University 👥
- **Funded by** [ATRIUM](https://atrium-research.eu/) 💰
- **Powered by** [LINDAT/CLARIAH-CZ](https://lindat.cz) Translation (CUBBITT) 🔗
