# Narrator-led history pipeline — Stage 2

This stage generates scripts, extracts claims for human review, and binds approval to saved files. It does not synthesize speech or render video. Kokoro, procedural character animation, measured timing, and the new Actions workflow belong to later stages. The existing V2 workflow remains manual with its schedule paused.

## Generate a draft

Install the existing requirements and configure an existing LLM provider key. No new dependencies are required for Stage 2.

```bash
python main.py --story --mode long --minutes 10 --topic "Your researched history topic" --sources sources.json --require-approval
python main.py --story --mode shorts --parts 4 --topic "Your researched history topic" --sources sources.json --require-approval
```

Long mode accepts 8–15 minutes; Shorts accepts 3–5 parts. These are word-count targets, not measured audio durations. Each chapter is generated separately with the outline and previous summaries for continuity. Characters act while one narrator tells the story. The `--story` switch selects this new path; legacy `--mode long` alone still selects V2. You can also run `python story_cli.py` directly.

`--script-only` and `--require-approval` express intent; Stage 2 always stops at a draft regardless of those flags.

## Supply evidence

Create a JSON list of sources you have actually read:

```json
[
  {
    "id": "source_1",
    "title": "Title of the historical source",
    "url": "https://example.org/replace-with-real-source",
    "excerpt": "Replace this with relevant evidence from the source."
  }
]
```

URLs are recorded, not downloaded or automatically verified. Only supplied source IDs may appear in generated claims. The reviewer must check that the original source actually supports the narration. Limits: 20 sources, 6,000 characters per excerpt, 24,000 excerpt characters total. Without sources, a draft can be generated but cannot be approved.

Draft directories contain `story.json` (authoritative script and scene instructions), `script.md`, `fact_check.json`, `fact_check.md`, `review.template.json`, and `metadata.json`. Timestamps remain empty until speech is measured. LLM confidence is advisory and cannot approve a claim.

## Review and approve

Copy `review.template.json` to `review.completed.json`. Fill in the reviewer, set `full_script_reviewed` after reviewing the entire narration, list checked source IDs, and decide every claim. Each decision needs evidence references and a note. A `qualified` claim must also be marked disputed or legendary in the script and audibly qualified in the narration. All scenes need checked evidence, even when the model extracts no claims from a scene.

```bash
python main.py --story --approve --draft DRAFT_DIRECTORY --review review.completed.json
python main.py --story --draft DRAFT_DIRECTORY --series-part 2
```

Approval writes a receipt bound to the story, claim report, and completed review hashes. Loading an approved draft revalidates these files; it never regenerates the story. The second command checks and loads a selected Shorts part only; it does not render it. Hashes detect changed files, not malicious users who can rewrite both the files and receipt. Repository access remains the security boundary.

If you edit a script, edit `story.json`, then obtain a fresh review package:

```bash
python main.py --story --recheck --draft DRAFT_DIRECTORY
```

Word counts are recalculated and the edited schema is validated. Rechecking does not carry forward approval. An identical existing draft directory is not overwritten.

## Queue and state

`topics.json` starts empty. Optional entries have `topic`, `enabled`, and optionally `sources_file` relative to the queue file. With no explicit topic, the first enabled unused topic is selected. Generated topics are deduplicated case-insensitively using `state/series.json`; an explicit `--topic` permits intentional retries. The exclusive state lock prevents overlapping local updates.

```bash
python main.py --story --suggest-topics 5 --niche "West African history"
```

Suggested topics are saved separately, disabled, and unverified; they are not automatically queued. Drafts and cached LLM responses are local outputs. Cache keys include the full generation prompt and evidence; cached responses are validated before reuse. Stage 4 must add workflow persistence for drafts and series state. Stage 2 does not push generated state automatically.

## Implementation and verification

| File | Responsibility |
| --- | --- |
| `llm.py` | JSON extraction, bounded retries, sequential provider fallback, cache |
| `story.py` | Outline, chapter generation, cast and scene schema validation |
| `factcheck.py` | Claim extraction and human-review validation |
| `story_store.py` | Atomic file writes, state lock, saved drafts and approval receipts |
| `story_cli.py` | Generation, recheck, approval, queue and resume commands |

Run offline tests with:

```bash
python -m unittest discover -s tests -v
```

The tests exercise malformed responses, provider fallback, continuity, evidence restrictions, approval requirements, changed-file rejection, queue locking, and part selection. They use synthetic evidence and mocked providers. They do not verify live LLM quality, historical accuracy, SDK network behavior, Kokoro audio, or video rendering.
