"""Stage 2 CLI. Produces reviewable scripts; audio/rendering arrives in Stage 3."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from factcheck import review_story
from llm import JsonLLM
from story import SYSTEM, build_story, text, validate_sources, validate_saved_story, words
from story_store import (approve, load_approved, next_topic, read_json, save_draft,
                         write_json, digest)

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['shorts', 'long'], default='long')
    parser.add_argument('--topic')
    parser.add_argument('--niche', default='world history')
    parser.add_argument('--minutes', type=float, default=10)
    parser.add_argument('--parts', type=int, default=4)
    parser.add_argument('--sources', type=Path, help='JSON list of source IDs, titles, URLs and excerpts')
    parser.add_argument('--topics', type=Path, default=ROOT / 'topics.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'stories')
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'state')
    parser.add_argument('--require-approval', action='store_true', help='Explicit review mode; always enforced in Stage 2')
    parser.add_argument('--script-only', action='store_true', help='Generate a draft (Stage 2 default)')
    parser.add_argument('--approve', action='store_true')
    parser.add_argument('--recheck', action='store_true', help='Re-review edited story.json into a new saved draft')
    parser.add_argument('--draft', type=Path, help='Exact saved draft directory; required for approval/resume')
    parser.add_argument('--review', type=Path, help='Completed review JSON, required with --approve')
    parser.add_argument('--series-part', type=int, help='Select an approved Shorts part without re-scripting')
    parser.add_argument('--suggest-topics', type=int, metavar='COUNT')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        if args.recheck:
            if not args.draft or args.approve:
                parser.error('--recheck requires --draft and cannot be combined with --approve')
            story = read_json(args.draft / 'story.json')
            for chapter in story['chapters']:
                chapter['word_count'] = sum(words(s['narration']) for s in chapter['scenes'])
            story['word_count'] = sum(c['word_count'] for c in story['chapters'])
            validate_saved_story(story)
            report = review_story(JsonLLM(cache_dir=ROOT / 'work' / 'story-cache'), story)
            print('New review draft: ' + str(save_draft(args.output, args.state_dir, story, report)))
            return 0
        if args.approve:
            if not args.draft or not args.review:
                parser.error('--approve requires --draft and --review')
            receipt = approve(args.draft, args.review, args.state_dir)
            print('Approved exact story SHA-256: ' + receipt['story_sha256'])
            print('Saved for Stage 3 rendering; no video was rendered.')
            return 0
        if args.draft:
            story = load_approved(args.draft, args.series_part)
            print(f"Approved script loaded: {story['outline']['title']}")
            print(f"Selected {len(story['chapters'])} chapter(s)/part(s). No video was rendered.")
            return 0
        if args.review or args.series_part is not None:
            parser.error('--review requires --approve; --series-part requires --draft')
        llm = JsonLLM(cache_dir=ROOT / 'work' / 'story-cache')
        if args.suggest_topics is not None:
            if not 1 <= args.suggest_topics <= 10:
                parser.error('--suggest-topics must be 1–10')
            def validate(data: dict) -> list[dict]:
                items = data['topics']
                if not isinstance(items, list) or len(items) != args.suggest_topics:
                    raise ValueError('Wrong number of topic suggestions')
                return [{'topic': text(x['topic'], 'topic', 500),
                         'research_question': text(x['research_question'], 'research question', 500),
                         'enabled': False} for x in items]
            suggestions = llm.generate(SYSTEM, {'task': 'suggest_topics', 'niche': args.niche,
                'count': args.suggest_topics, 'schema': {'topics': [{'topic': 'topic',
                    'research_question': 'what primary evidence should be sought?'}]}}, validate)
            destination = args.output / ('topic-suggestions-' + digest({'topics': suggestions})[:12] + '.json')
            write_json(destination, suggestions)
            print(f'Research suggestions saved (not verified or queued): {destination}')
            return 0
        topic = args.topic
        sources_path = args.sources
        if not topic:
            item = next_topic(args.topics, args.state_dir)
            topic = item['topic']
            if not sources_path and item.get('sources_file'):
                sources_path = args.topics.resolve().parent / item['sources_file']
        sources = validate_sources(read_json(sources_path)) if sources_path else []
        if not sources:
            logging.warning('No source evidence supplied. Draft cannot be approved until supported and re-reviewed.')
        story = build_story(llm, topic, args.mode, args.minutes, args.parts, sources, args.niche)
        report = review_story(llm, story)
        draft = save_draft(args.output, args.state_dir, story, report)
        print(f'Draft saved: {draft}\nReview script.md and fact_check.md; complete review.template.json.')
        print('Stopped before audio/rendering. Duration remains unmeasured.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        logging.error('%s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
