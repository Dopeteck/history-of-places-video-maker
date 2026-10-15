"""Offline tests: all narrative and evidence here are synthetic fixtures."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from factcheck import review_story, review_template, validate_review
from llm import JsonLLM, ProviderHTTPError, extract_json
from story import build_story, digest, validate_sources
from story_store import approve, load_approved, next_topic, read_json, save_draft, state_lock, write_json
from story_cli import main

SOURCES = [{'id': 'fixture', 'title': 'Synthetic evidence', 'url': 'https://example.org/test', 'excerpt': 'Test only.'}]
PROVIDERS = [{'name': n, 'env_key': 'TEST_LLM_KEY', 'type': 'openai', 'models': [n]} for n in ['first', 'second']]

class FakeLLM:
    def __init__(self): self.prompts = []
    def generate(self, system, prompt, validate):
        # Snapshot prompts, so later list mutation cannot mask continuity bugs.
        self.prompts.append(json.loads(json.dumps(prompt)))
        if prompt['task'] == 'outline':
            data = {'title': 'Synthetic pipeline test', 'hook': 'A test begins.', 'logline': 'Test only.',
                'cast': [{'id': 'actor', 'name': 'Actor', 'role': 'test', 'trait': 'crown', 'accessory': 'crown', 'accent': '#E0A526'}],
                'chapters': [{'title': f'Section {i}', 'purpose': 'test', 'key_events': ['test event']} for i in range(prompt['count'])], 'ending': 'Test ends.'}
        elif prompt['task'] == 'chapter':
            minimum = prompt['word_range'][0]
            count = max(4, (minimum + 39) // 40)
            per_scene = (minimum + count - 1) // count
            data = {'summary': 'Continuity summary.', 'scenes': [
                {'narration': ' '.join(['synthetic'] * per_scene) + '.',
                 'characters_on_screen': [{'id': 'actor', 'x': 0.3, 'pose': 'pointing', 'expression': 'neutral'}],
                 'background': 'palace', 'camera': 'push_in', 'effects': ['cut'], 'props': [],
                 'emphasis_words': ['synthetic'], 'source_ids': ['fixture'], 'uncertainty': 'documented'} for _ in range(count)]}
        elif prompt['task'] == 'claim_review':
            data = {'scenes': [{'scene_id': s['scene_id'], 'claims': [{'claim': 'Test claim', 'confidence': 1.0,
                'source_ids': ['fixture'], 'what_to_verify': 'Compare fixture'}]} for s in prompt['scenes']]}
        else: raise AssertionError('Unexpected task')
        return validate(data)

def fixture():
    llm = FakeLLM()
    story = build_story(llm, 'Synthetic testing', 'shorts', 10, 4, SOURCES)
    report = review_story(llm, story)
    review = review_template(story, report)
    review.update(reviewer='Test reviewer', full_script_reviewed=True, sources_checked=['fixture'])
    for row in review['claims']: row.update(decision='verified', source_ids=['fixture'], note='Compared fixture.')
    return story, report, review

class PipelineTests(unittest.TestCase):
    def test_short_series_continuity(self):
        llm = FakeLLM()
        story = build_story(llm, 'Testing', 'shorts', 10, 4, SOURCES)
        self.assertEqual(len(story['chapters']), 4)
        self.assertTrue(all(170 <= c['word_count'] <= 210 for c in story['chapters']))
        self.assertEqual([len(p['previous_summaries']) for p in llm.prompts if p['task']=='chapter'], [0,1,2,2])
        self.assertEqual(story['timing_status'], 'unmeasured_until_tts')
    def test_long_is_chunked(self):
        llm = FakeLLM(); story = build_story(llm, 'Testing', 'long', 10, 4, SOURCES)
        self.assertEqual(len(story['chapters']), 5)
        self.assertTrue(1300 <= story['word_count'] <= 1500)
    def test_invented_sources_rejected(self):
        with self.assertRaises(ValueError): build_story(FakeLLM(), 'Testing', 'shorts', 10, 4, [])
    def test_invalid_sources_rejected(self):
        self.assertEqual(validate_sources(SOURCES), SOURCES)
        for url in ['file:///etc/passwd', 'https://user:secret@example.org']:
            with self.assertRaises(ValueError): validate_sources([{**SOURCES[0], 'url': url}])
        with self.assertRaises(ValueError): validate_sources(SOURCES * 2)
    def test_confidence_not_approval(self):
        story, report, _ = fixture()
        self.assertTrue(all(c['status']=='needs_human_review' for c in report['claims']))
        with self.assertRaises(ValueError): validate_review(story, report, review_template(story, report))
    def test_pending_and_missing_claims_block(self):
        story, report, review = fixture(); review['claims'][0]['decision']='pending'
        with self.assertRaises(ValueError): validate_review(story, report, review)
        review['claims'].pop(0)
        with self.assertRaises(ValueError): validate_review(story, report, review)
    def test_unchecked_evidence_blocks(self):
        story, report, review = fixture(); review['sources_checked']=[]
        with self.assertRaises(ValueError): validate_review(story, report, review)
    def test_qualified_requires_audible_hedge(self):
        story, report, review = fixture(); review['claims'][0]['decision']='qualified'
        story['chapters'][0]['scenes'][0]['uncertainty']='legend'
        report['story_sha256']=digest(story)
        review['story_sha256'],review['report_sha256']=digest(story),digest(report)
        with self.assertRaises(ValueError): validate_review(story, report, review)
    def test_approve_resume_exact_part(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); story,report,review=fixture()
            draft=save_draft(root/'out',root/'state',story,report)
            write_json(root/'review.json',review); approve(draft,root/'review.json',root/'state')
            self.assertEqual(load_approved(draft,3)['chapters'][0]['number'],3)
            self.assertEqual(read_json(root/'state/series.json')['stories'][draft.name]['status'],'approved')
            with self.assertRaises(ValueError): load_approved(draft,5)
            self.assertFalse(any(root.rglob('*.mp4')))
    def test_tampering_blocked(self):
        for target in ['story.json','fact_check.json','review.json']:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                root=Path(d); story,report,review=fixture()
                draft=save_draft(root/'out',root/'state',story,report)
                write_json(root/'review.json',review); approve(draft,root/'review.json',root/'state')
                obj=read_json(draft/target);obj['tampered']=True;write_json(draft/target,obj)
                with self.assertRaises(ValueError):load_approved(draft)
    def test_queue_and_lock(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);story,report,_=fixture();save_draft(root/'out',root/'state',story,report)
            write_json(root/'topics.json',[{'topic':' SYNTHETIC TESTING '},{'topic':'Next topic'}])
            self.assertEqual(next_topic(root/'topics.json',root/'state')['topic'],'Next topic')
            with state_lock(root/'state'):
                with self.assertRaises(RuntimeError):
                    with state_lock(root/'state'):pass
    def test_cli_review_required(self):
        with self.assertRaises(SystemExit) as e:main(['--approve'])
        self.assertEqual(e.exception.code,2)

class ProviderTests(unittest.TestCase):
    def test_json_extraction(self):
        self.assertEqual(extract_json('<think>ignored</think>```json\n{"a":1}\n``` trailing'),{'a':1})
        with self.assertRaises(ValueError):extract_json('no JSON')
    def test_missing_keys_no_network(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'No script API key'):
                JsonLLM(call=lambda *a:self.fail('network'),providers=PROVIDERS).generate('',{},lambda d:d)
    def test_backoff_and_fallback(self):
        delays=[];attempts=[]
        def call(provider,model,system,prompt):
            attempts.append(model)
            if model=='first':raise ProviderHTTPError(429, '{"error":"quota"}', 'rate limited')
            return '{"ok":true}'
        with patch.dict(os.environ,{'TEST_LLM_KEY':'test'}):
            result=JsonLLM(call=call,sleep=delays.append,providers=PROVIDERS).generate('',{},lambda d:d)
        self.assertEqual(attempts,['first','first','second']);self.assertTrue(result['ok'])
        self.assertEqual(len(delays),1)
        self.assertGreaterEqual(delays[0],0.5)
        self.assertLess(delays[0],2.0)

    def test_client_errors_are_not_retried(self):
        for status in (400, 404):
            with self.subTest(status=status):
                attempts=[]
                def call(provider,model,system,prompt):
                    attempts.append(model)
                    raise ProviderHTTPError(status, '{"error":"bad request"}', 'client error')
                with patch.dict(os.environ,{'TEST_LLM_KEY':'test'}):
                    with self.assertRaisesRegex(RuntimeError, 'All script providers failed'):
                        JsonLLM(call=call,sleep=lambda _:None,providers=PROVIDERS).generate('',{},lambda d:d)
                self.assertEqual(attempts,['first','second'])

    def test_budget_exhausted_before_provider_call(self):
        attempts=[]
        def call(*args):
            attempts.append(args)
            return '{"ok":true}'
        with patch.dict(os.environ,{'TEST_LLM_KEY':'test'}), patch('llm.config.PROVIDER_TOTAL_BUDGET_SECONDS', 0):
            with self.assertRaisesRegex(RuntimeError, 'time budget'):
                JsonLLM(call=call,providers=PROVIDERS).generate('',{},lambda d:d)
        self.assertEqual(attempts,[])
    def test_malformed_repair_and_cache(self):
        replies=iter(['bad JSON','{"ok":true}'])
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'TEST_LLM_KEY':'test'}):
            client=JsonLLM(call=lambda *a:next(replies),sleep=lambda _:None,providers=PROVIDERS,cache_dir=Path(d))
            self.assertEqual(client.generate('system',{'test':1},lambda d:d),{'ok':True})
            client.call=lambda *a:self.fail('network on cache hit')
            self.assertEqual(client.generate('system',{'test':1},lambda d:d),{'ok':True})

if __name__=='__main__':unittest.main()
