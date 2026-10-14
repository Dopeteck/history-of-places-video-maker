"""Optional Telegram delivery. Credentials stay out of command arguments and logs."""
from __future__ import annotations
import os
from pathlib import Path
import time
import requests
from story_store import read_json

LIMIT=49_000_000

class Telegram:
    def __init__(self, token: str, chat: str, session=None, sleep=time.sleep):
        self.base='https://api.telegram.org/bot'+token+'/'
        self.chat=chat;self.session=session or requests.Session();self.sleep=sleep
    def send(self,method: str,text: str='',file: Path | None=None):
        for attempt in range(2):
            data={'chat_id':self.chat};handle=None
            if file:
                if file.stat().st_size>=LIMIT:raise ValueError('Telegram attachment is too large')
                data['caption']=text[:1000]
                if method=='sendVideo':data['supports_streaming']='true'
                handle=file.open('rb');files={'video' if method=='sendVideo' else 'document':handle}
            else:data['text']=text[:4000];files=None
            try:
                response=self.session.post(self.base+method,data=data,files=files,timeout=(10,120))
                payload=response.json()
            except (requests.RequestException,ValueError):
                # An uncertain network outcome is not retried: avoid duplicate sends.
                raise RuntimeError('Telegram transport failed; check the workflow artifact') from None
            finally:
                if handle:handle.close()
            if response.status_code==429 and attempt==0:
                self.sleep(min(60,max(1,int(payload.get('parameters',{}).get('retry_after',5)))));continue
            if not response.ok or not payload.get('ok'):raise RuntimeError('Telegram rejected delivery; check bot/chat permissions')
            return
        raise RuntimeError('Telegram rate limit persisted')

def notify(manifest: dict, bot: Telegram, artifact_url: str, failure=False):
    link=artifact_url or manifest.get('run_url','')
    if failure:
        bot.send('sendMessage','Stickman pipeline failed. Logs and recovery files: '+link);return
    status=manifest.get('status','unknown')
    bot.send('sendMessage',f"Stickman pipeline: {status}\nDraft: {manifest.get('draft_id','—')}\n{link}\nGitHub artifacts require your GitHub login and expire after 7 days.")
    if status=='awaiting_review':
        folder=Path(manifest['draft'])
        for name in ('script.md','fact_check.md','review.template.json'):
            bot.send('sendDocument',file=folder/name,text=name)
    elif manifest.get('mode')=='shorts' and manifest.get('video'):
        video=Path(manifest['video'])
        if video.is_file() and video.stat().st_size<LIMIT:
            bot.send('sendVideo',file=video,text='Stickman preview' if status=='smoke_passed' else status)
    # Long videos are always links, regardless of size.

if __name__=='__main__':
    token=os.getenv('TELEGRAM_BOT_TOKEN');chat=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:print('Telegram skipped: optional bot/chat secrets are not configured.')
    else:
        path=Path('output/run.json');manifest=read_json(path) if path.exists() else {'run_url':os.getenv('RUN_URL','')}
        try:notify(manifest,Telegram(token,chat),os.getenv('ARTIFACT_URL',''),os.getenv('PIPELINE_FAILED')=='true')
        except Exception as exc:
            print('Telegram delivery failed: '+type(exc).__name__)
            raise SystemExit(1)
