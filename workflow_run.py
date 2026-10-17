"""Safe Actions entry point. All dispatch input is data, never shell source."""
from __future__ import annotations
import os
from pathlib import Path
import re
import sys
import config
from story_cli import main as cli
from story_store import approve, load_approved, read_json, record_render, write_json

ROOT=Path(__file__).resolve().parent

def repository_path(root: Path, value: str, prefix: str) -> Path:
    """Only permit files in the intended repository subtree, including symlinks."""
    path=(root/value).resolve()
    if not path.is_relative_to((root/prefix).resolve()) or not path.is_file():
        raise ValueError(f'Expected an existing file inside {prefix}/')
    return path

def draft_path(root: Path, value: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{16}-[a-f0-9]{8}',value):
        raise ValueError('Use the exact draft ID printed by the draft run')
    path=root/'state'/'drafts'/value
    if not path.is_dir() or path.is_symlink():raise ValueError('Saved draft not found')
    return path

def next_approved(root: Path):
    path=root/'state'/'series.json'
    state=read_json(path) if path.exists() else {'stories':{}}
    for sid,item in sorted(state['stories'].items()):
        if item['status'] not in {'approved','partially_rendered'}:continue
        draft=draft_path(root,sid);story=load_approved(draft)
        if story['mode']=='long':return sid,None
        for n in range(1,len(story['chapters'])+1):
            if n not in item.get('rendered_parts',[]):return sid,n
    return None

def run(root: Path=ROOT, env=None) -> dict:
    env=os.environ if env is None else env
    action=env.get('STORY_ACTION','smoke');mode=env.get('STORY_MODE','long')
    state=root/'state';drafts=state/'drafts';out=root/'output';out.mkdir(exist_ok=True)
    run_url=env.get('RUN_URL','')
    common=['--state-dir',str(state)]
    if env.get('GITHUB_EVENT_NAME')=='push':action='smoke'
    if env.get('GITHUB_EVENT_NAME')=='schedule':action='auto'
    if action=='auto':
        pending=next_approved(root)
        if pending:
            sid,part=pending;action='render'
        else:
            sid='';part=None;action='draft'
            queue=read_json(root/'topics.json')
            used={x['topic'].strip().casefold() for x in read_json(state/'series.json')['stories'].values()}
            if not any(x.get('enabled',True) and x['topic'].strip().casefold() not in used for x in queue):
                result={'status':'idle','run_url':run_url};write_json(out/'run.json',result);return result
    else:
        sid=env.get('STORY_DRAFT','').strip();part=int(env.get('STORY_PART','0') or 0) or None
    if mode not in {'shorts','long'}:raise ValueError('Unknown mode')
    if action=='smoke':
        # Tests both formats without LLM/TTS network access or repository state changes.
        for profile in ('shorts','long'):
            if cli(['--smoke-test','--mode',profile,'--output',str(out),*common]):raise RuntimeError('Smoke render failed')
        result={'status':'smoke_passed','mode':'shorts','video':str(out/'smoke-shorts'/'video.mp4'),'run_url':run_url}
    elif action in {'draft','recheck'}:
        drafts.mkdir(parents=True,exist_ok=True);before=set(drafts.iterdir())
        args=['--output',str(drafts),*common]
        if action=='recheck':args+=['--recheck','--draft',str(draft_path(root,sid))]
        else:
            args+=['--mode',mode,'--minutes',env.get('STORY_MINUTES','10'),
                   '--parts',env.get('STORY_PARTS','4'),'--topics',str(root/'topics.json'),'--require-approval','--niche',config.STORY_NICHE]
            topic=env.get('STORY_TOPIC','').strip()
            if topic:args+=['--topic',topic]
            source=env.get('STORY_SOURCES','').strip()
            if source:args+=['--sources',str(repository_path(root,source,'research'))]
        if cli(args):raise RuntimeError('Draft/recheck failed; inspect the logs')
        created=set(drafts.iterdir())-before
        if len(created)!=1:raise RuntimeError('Expected exactly one new draft')
        draft=created.pop();result={'status':'awaiting_review','draft_id':draft.name,'draft':str(draft),'run_url':run_url}
    elif action=='preview':
        # Render an explicitly unapproved draft for private visual review only.
        # Never create an approval receipt or update approved-render state here.
        draft=draft_path(root,sid)
        story=read_json(draft/'story.json')
        if story.get('mode') not in {'shorts','long'}:raise ValueError('Saved draft has an invalid mode')
        if story['mode']=='shorts' and part is None:raise ValueError('Select a Shorts part (1–5)')
        if part is not None and story['mode']=='shorts':
            chapters=story.get('chapters',[])
            selected=next((ch for ch in chapters if ch.get('number')==part),None)
            if selected is None:raise ValueError('Requested Shorts part is not in this draft')
            story={**story,'chapters':[selected]}
        from render import render_story
        destination=out/('preview-'+sid+(f'-part-{part}' if part else ''))
        video=render_story(story,destination,config.STORY_VOICE,config.STORY_SPEED,preview=True)
        metadata=read_json(destination/'render-metadata.json')
        metadata['disclosure']='UNAPPROVED PREVIEW — historical claims have not been cleared for publication.'
        metadata['preview_only']=True
        write_json(destination/'render-metadata.json',metadata)
        description=destination/'description.txt'
        existing=description.read_text(encoding='utf-8') if description.exists() else ''
        description.write_text('PREVIEW ONLY — NOT FACT-CHECKED OR APPROVED FOR PUBLICATION.\n\n'+existing,encoding='utf-8')
        (destination/'PREVIEW-ONLY.txt').write_text(
            'This video is an unapproved visual preview. Historical claims have not been cleared for publication.\n',
            encoding='utf-8')
        result={'status':'preview_rendered','draft_id':sid,'mode':story['mode'],'part':part,
                'video':str(video),'preview_only':True,'run_url':run_url}
    elif action=='render':
        draft=draft_path(root,sid)
        if env.get('STORY_APPROVE','false').lower()=='true':
            review=repository_path(root,env.get('STORY_REVIEW',''),'state/reviews')
            approve(draft,review,state)
        story=load_approved(draft,part)
        if story['mode']=='shorts' and part is None:raise ValueError('Select a Shorts part (1–5)')
        from render import render_story
        destination=out/('render-'+sid+(f'-part-{part}' if part else ''))
        video=render_story(story,destination,config.STORY_VOICE,config.STORY_SPEED)
        metadata=read_json(destination/'render-metadata.json')
        record_render(state,draft,part,metadata,run_url)
        result={'status':'rendered' if metadata['duration_in_target'] else 'needs_timing_review',
                'draft_id':sid,'mode':story['mode'],'part':part,'video':str(video),'run_url':run_url}
    else:raise ValueError('Unknown workflow action')
    write_json(out/'run.json',result)
    summary=env.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:
            f.write(f"## Story pipeline\n\nStatus: `{result['status']}`\n\n")
            if result.get('draft_id'):f.write(f"Draft ID: `{result['draft_id']}`\n\nComplete the claim review before rendering.\n")
    return result

if __name__=='__main__':
    try:run()
    except Exception as exc:
        # API exception text can contain credentials: expose type and safe application messages only.
        message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
        print('Pipeline failed: '+message,file=sys.stderr);sys.exit(1)
