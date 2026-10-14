"""Atomic stage-2 artifacts, exact-content approval, and topic/series state."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from factcheck import markdown, review_template, validate_review
from story import digest


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


@contextmanager
def state_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.series.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError('Series state is locked. Retry after the other job finishes; '
                           'remove a stale lock only after confirming no job is active.') from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def update_state(root: Path, story_id: str, story: dict, status: str) -> None:
    with state_lock(root):
        path = root / 'series.json'
        state = read_json(path) if path.exists() else {'schema_version': 1, 'stories': {}}
        previous = state['stories'].get(story_id, {})
        state['stories'][story_id] = {**previous, 'topic': story['topic'], 'mode': story['mode'],
                                      'sha256': digest(story), 'status': status,
                                      'parts': len(story['chapters']) if story['mode'] == 'shorts' else 1,
                                      'rendered_parts': previous.get('rendered_parts', []),
                                      'updated_at': datetime.now(timezone.utc).isoformat()}
        write_json(path, state)


def next_topic(queue: Path, state_root: Path) -> dict:
    topics = read_json(queue)
    if not isinstance(topics, list):
        raise ValueError('topics.json must contain a list')
    path = state_root / 'series.json'
    state = read_json(path) if path.exists() else {'stories': {}}
    used = {s['topic'].strip().casefold() for s in state['stories'].values()}
    for item in topics:
        if not isinstance(item, dict) or not isinstance(item.get('topic'), str) or not item['topic'].strip():
            raise ValueError('Each queue entry requires a nonempty topic string')
        if item.get('enabled', True) and item['topic'].strip().casefold() not in used:
            return item
    raise ValueError('No unused enabled topics. Add an entry to topics.json or pass --topic')


def script_markdown(story: dict) -> str:
    lines = ['# ' + story['outline']['title'], '', story['outline']['logline'], '']
    for ch in story['chapters']:
        label = f"Part {ch['number']}/{len(story['chapters'])}" if story['mode'] == 'shorts' else f"Chapter {ch['number']}"
        lines.extend([f"## {label}: {ch['title']}", ''])
        lines.extend(s['narration'] + '\n' for s in ch['scenes'])
    lines += ['', story['disclosure'], '', 'Duration is a target until narration audio is measured.']
    return '\n'.join(lines) + '\n'


def save_draft(output: Path, state_root: Path, story: dict, report: dict) -> Path:
    sha = digest(story)
    if report['story_sha256'] != sha:
        raise ValueError('Cannot save a review for different story content')
    dest = output / (sha[:16] + '-' + digest(report)[:8])
    dest.mkdir(parents=True, exist_ok=False)
    write_json(dest / 'story.json', story)
    write_json(dest / 'fact_check.json', report)
    write_json(dest / 'review.template.json', review_template(story, report))
    atomic_write(dest / 'script.md', script_markdown(story))
    atomic_write(dest / 'fact_check.md', markdown(story, report))
    # Metadata is provisional. Chapter timestamps must come from rendered segment durations.
    write_json(dest / 'metadata.json', {'title': story['outline']['title'],
        'description': story['outline']['logline'], 'disclosure': story['disclosure'],
        'sources': [{k: s[k] for k in ('id', 'title', 'url')} for s in story['sources']],
        'chapter_timestamps': [], 'timing_status': 'unmeasured_until_tts',
        'hashtags': ['#History', '#Stickman'], 'tags': ['history', 'animated history', 'stickman'],
        'parts': [{'number': ch['number'], 'title':
                   (f"Part {ch['number']}/{len(story['chapters'])}: " + ch['title'])[:70]}
                  for ch in story['chapters']] if story['mode'] == 'shorts' else []})
    update_state(state_root, dest.name, story, 'awaiting_review')
    return dest


def approve(draft: Path, review_path: Path, state_root: Path) -> dict:
    story, report, review = (read_json(draft / 'story.json'),
                             read_json(draft / 'fact_check.json'), read_json(review_path))
    validate_review(story, report, review)
    receipt = {'story_sha256': digest(story), 'report_sha256': digest(report),
               'review_sha256': digest(review), 'reviewer': review['reviewer'],
               'approved_at': datetime.now(timezone.utc).isoformat()}
    write_json(draft / 'review.json', review)
    write_json(draft / 'approval.json', receipt)
    update_state(state_root, draft.name, story, 'approved')
    return receipt


def load_approved(draft: Path, part: int | None = None) -> dict:
    """Stage-3 entry point: revalidate all hashes on every use, never regenerate."""
    story, report, review, receipt = [read_json(draft / name) for name in
                                      ('story.json', 'fact_check.json', 'review.json', 'approval.json')]
    validate_review(story, report, review)
    for key, value in [('story_sha256', story), ('report_sha256', report), ('review_sha256', review)]:
        if receipt.get(key) != digest(value):
            raise ValueError('Approved content changed; obtain a fresh review and approval')
    if part is not None:
        if story['mode'] != 'shorts' or not 1 <= part <= len(story['chapters']):
            raise ValueError('Part is outside this Shorts series')
        return {**story, 'selected_part': part, 'chapters': [story['chapters'][part - 1]]}
    return story
