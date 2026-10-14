"""Workflow integration tests with synthetic scripts; never sends real messages."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import requests
from test_story_pipeline import fixture
from story_store import save_draft,approve,read_json,write_json,record_render
from workflow_run import run,next_approved,repository_path
from telegram_notify import Telegram,notify,LIMIT

class WorkflowTests(unittest.TestCase):
    def make_draft(self,root,approved=True):
        story,report,review=fixture();draft=save_draft(root/'state/drafts',root/'state',story,report)
        folder=root/'state/reviews';folder.mkdir(parents=True);review_path=folder/'completed.json';write_json(review_path,review)
        if approved:approve(draft,review_path,root/'state')
        return draft
    def test_pending_is_not_auto_rendered(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.make_draft(root,False);self.assertIsNone(next_approved(root))
    def test_render_tracks_part_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);draft=self.make_draft(root)
            def render(story,destination,*args):
                self.assertEqual(story['selected_part'],1);destination.mkdir(parents=True)
                write_json(destination/'render-metadata.json',{'duration_seconds':75,'duration_in_target':True})
                video=destination/'video.mp4';video.write_bytes(b'test');return video
            with patch('render.render_story',side_effect=render):
                result=run(root,{'STORY_ACTION':'render','STORY_DRAFT':draft.name,'STORY_PART':'1'})
            self.assertEqual(result['status'],'rendered');self.assertEqual(next_approved(root),(draft.name,2))
            state=read_json(root/'state/series.json')['stories'][draft.name]
            self.assertEqual(state['rendered_parts'],[1]);self.assertEqual(state['status'],'partially_rendered')
    def test_bad_duration_requires_attention(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);draft=self.make_draft(root)
            record_render(root/'state',draft,1,{'duration_seconds':30,'duration_in_target':False})
            self.assertIsNone(next_approved(root));self.assertEqual(read_json(root/'state/series.json')['stories'][draft.name]['rendered_parts'],[])
    def test_render_failure_does_not_mark_done(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);draft=self.make_draft(root)
            with patch('render.render_story',side_effect=RuntimeError('FFmpeg failed')):
                with self.assertRaises(RuntimeError):run(root,{'STORY_ACTION':'render','STORY_DRAFT':draft.name,'STORY_PART':'1'})
            self.assertEqual(next_approved(root),(draft.name,1))
    def test_boolean_is_not_review(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);draft=self.make_draft(root,False)
            with patch('render.render_story') as render:
                with self.assertRaises(ValueError):run(root,{'STORY_ACTION':'render','STORY_DRAFT':draft.name,'STORY_PART':'1','STORY_APPROVE':'true'})
                render.assert_not_called()
    def test_input_paths_confined(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'research').mkdir();(root/'secret.txt').write_text('synthetic')
            (root/'research/link.json').symlink_to(root/'secret.txt')
            for path in ('../secret.txt','secret.txt','research/link.json'):
                with self.assertRaises(ValueError):repository_path(root,path,'research')
    def test_schedule_empty_queue_is_idle(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);write_json(root/'state/series.json',{'stories':{}});write_json(root/'topics.json',[])
            self.assertEqual(run(root,{'GITHUB_EVENT_NAME':'schedule'})['status'],'idle')

class TelegramTests(unittest.TestCase):
    def test_long_video_links_only(self):
        bot=Mock();notify({'status':'rendered','mode':'long','video':'tiny.mp4'},bot,'https://example.org/artifact')
        self.assertEqual(bot.send.call_count,1);self.assertEqual(bot.send.call_args.args[0],'sendMessage')
    def test_small_short_is_attached(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'v.mp4';p.write_bytes(b'test');bot=Mock()
            notify({'status':'rendered','mode':'shorts','video':str(p)},bot,'https://example.org/artifact')
            self.assertEqual(bot.send.call_args.args[0],'sendVideo')
    def test_oversize_short_link_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'v.mp4'
            with p.open('wb') as f:f.truncate(LIMIT)
            bot=Mock();notify({'status':'rendered','mode':'shorts','video':str(p)},bot,'https://example.org/artifact');self.assertEqual(bot.send.call_count,1)
    def test_rate_limit_and_ambiguous_failure(self):
        session=Mock();wait=Mock()
        limited=Mock(status_code=429,ok=False);limited.json.return_value={'ok':False,'parameters':{'retry_after':1}}
        good=Mock(status_code=200,ok=True);good.json.return_value={'ok':True}
        session.post.side_effect=[limited,good];bot=Telegram('secret','123',session,wait);bot.send('sendMessage','test')
        wait.assert_called_once_with(1);self.assertEqual(session.post.call_count,2)
        session.post.side_effect=requests.Timeout('token secret')
        with self.assertRaisesRegex(RuntimeError,'Telegram transport failed'):bot.send('sendMessage','test')
        self.assertEqual(session.post.call_count,3)
    def test_failed_api_response_is_not_success(self):
        response=Mock(status_code=200,ok=True);response.json.return_value={'ok':False};session=Mock();session.post.return_value=response
        with self.assertRaises(RuntimeError):Telegram('secret','123',session).send('sendMessage','test')

if __name__=='__main__':unittest.main()
