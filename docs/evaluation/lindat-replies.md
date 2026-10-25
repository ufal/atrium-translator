# LINDAT's replies: what was found and how it is watched

For operators and anyone who wonders why a run logs re-requested replies. It records what the sample refresh of
2026-09-26 found about the public LINDAT translation endpoint, how it was confirmed, how the output guard was
calibrated, and the daily check that watches the endpoint now. How the guard works:
[The degenerate-output guard](../how-it-works.md#the-degenerate-output-guard).

## What the refresh found

On the 2026-09-26 sample refresh about a third of all LINDAT replies came back as one Czech word repeated up to
about 150 times (`"pravidla pravidla …"`), for one-word headings and for whole sentences, on the ALTO and on the
metadata path, and nondeterministically: the same field was garbage in one run and correct in the next. Nothing looked
at reply content, so the garbage went into the XML and the log, and, through the ALTO line anchors, into the
word-to-box alignment of every block on the page: 2,489 blanked `String`s instead of 224.

**What was behind it.** Sequential runs made the pattern visible: the first request of every batch came back
degenerate, its byte-identical re-request succeeded, and two runs agreed reply for reply (the same inputs failed with
the same token counts). Garbage that is deterministic for a given input, yet cured by repeating the identical
request, is the signature of **one broken replica behind a round-robin load balancer**: every other request lands on
it. That is also why the concurrent sample runs of 2026-09-26 looked randomly about 30 % broken. The output is correct
either way, since the guard recovers each reply at the cost of a second request, but the fix belongs to the service.

**Confirmed on 2026-09-26 (11:51 UTC)** against `…/api/v2/models/cs-en`: 12 requests with a fresh connection each
and 12 over one keep-alive session both came back `✗✓✗✓✗✓✗✓✗✓✗✓`. The bad replies are 109 tokens of `pravidla` for a
three-word input in about 1.6 s (the decoder runs to its length cap); the good ones take about 0.3 s; the only server
header is `nginx/1.30.1`. The balancer rotates **per request, even inside one connection**, so nothing on the client
side can avoid the broken replica, and the next request always lands on the healthy one. That is why the guard's
first re-request is immediate, and, in every run so far, sufficient.

## Checking the endpoint yourself

`eval/lindat_probe.py` sends one text N times to the live endpoint, over fresh connections and over one session, and
prints the pattern, with a report to hand to the service's operators:

```bash
python -m eval.lindat_probe            # e.g. "fresh: ✗✓✗✓✗✓… → alternating — one of two replicas broken"
python -m eval.lindat_probe --text daily --n 12 --pause 0.5 --max-bad 0   # the daily check; exit 1 on any bad reply
```

`--n` sets the requests per mode (12), `--model` the pair (`cs-en`), `--pause` the seconds between requests. A reply
is bad when the guard rejects it, or when it fails the probe's content checks, which need the languages: every number
kept, not the source repeated, no Czech-only letter left in the reply. Without `--max-bad` the probe only reports.

## The daily check

`.github/workflows/lindat-daily.yml` runs the probe every day: one Czech sentence (`--text daily`, "Výzkum v roce
1932 odkryl 3 hroby a 120 střepů.") sent 24 times, 12 over fresh connections and 12 over one session; any bad reply
fails the run (`--max-bad 0`), and a scheduled failure opens or updates the lane's issue through the hub's
`lane-failure-issue` action (atrium-project#78, #81). It installs only `requests`. It is on the `test` branch since 2026-10-22 and arrives with the next release.

## How the guard was calibrated

The guard's rules were calibrated at no false flag on all 1,193 ALTO blocks of the June sample and the 37 metadata
fields, with every looping reply caught. The mixed-script rule (a word with both Latin and Cyrillic letters) was
checked on the 4,264 logged rows of `data_samples/`, where its one hit is the `Ostrůв` that a self-hosted EuroLLM
run of 2026-09-28 had logged as `ok`. In the AMČR production-shape run of 2026-10-19 (15 records, LINDAT), 35 replies
were degenerate, re-requested and recovered, so every field was `ok`
([data_samples/README.md](../../data_samples/README.md)).

These numbers show that the guard catches the failure shapes seen so far without rejecting good replies on these
samples. They are not a measure of translation quality.
