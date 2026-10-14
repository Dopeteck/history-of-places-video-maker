"""Stream frames through MoviePy/FFmpeg, then concatenate measured chapter segments."""
from pathlib import Path
import json
import math
import shutil
import subprocess
import time
import wave
import numpy as np
from captions import build_ass,word_timings
from scenes import Scene,title_card
from tts import Narrator,tone,RATE
from story_store import write_json
FPS=24
TITLE_SECONDS=2.5

def command(args,cwd=None):
    try:
        p=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=5400)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('FFmpeg exceeded the 90-minute command limit') from exc
    if p.returncode:raise RuntimeError('FFmpeg failed: '+p.stderr[-2500:])

def media_duration(path):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if p.returncode:raise RuntimeError('Unable to inspect rendered video: '+p.stderr[-1000:])
    value=float(json.loads(p.stdout)['format']['duration'])
    if not math.isfinite(value) or value<=0:raise ValueError('Invalid media duration')
    return value

def timeline(durations,title=TITLE_SECONDS):
    cursor=title;result=[]
    for seconds in durations:
        if not math.isfinite(seconds) or seconds<=0:raise ValueError('Invalid scene duration')
        length=math.ceil(seconds*FPS)/FPS;result.append({'start':cursor,'duration':length});cursor+=length
    return result,cursor

def chapter_audio(path,audios,entries,total):
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(RATE);cursor=0
        for audio,entry in zip(audios,entries):
            start=round(entry['start']*RATE);out.writeframes(b'\0\0'*(start-cursor))
            with wave.open(audio['path'],'rb') as src:
                if (src.getnchannels(),src.getsampwidth(),src.getframerate())!=(1,2,RATE):raise ValueError('Expected mono 24kHz PCM WAV')
                data=src.readframes(src.getnframes());out.writeframes(data);cursor=start+len(data)//2
        out.writeframes(b'\0\0'*max(0,round(total*RATE)-cursor))

def render_chapter(chapter,cast,size,work,voice,mode,part_count,assets,captions=False,smoke=False):
    from moviepy.video.VideoClip import VideoClip
    work.mkdir(parents=True,exist_ok=True);audios=[]
    for i,s in enumerate(chapter['scenes']):
        if smoke:
            p=work/f'tone-{i}.wav';tone(p,4.375);audio={'path':str(p.resolve()),'duration':4.375,'words':word_timings(s['narration'],4.375),'alignment':'synthetic_test'}
        else:audio=voice.synthesize(s['narration'],work.parent/'audio-cache')
        audios.append(audio)
    entries,total=timeline([a['duration'] for a in audios]);chapter_audio(work/'audio.wav',audios,entries,total)
    label=f"Part {chapter['number']}/{part_count}" if mode=='shorts' else f"Chapter {chapter['number']}"
    card=title_card(chapter['title'],label,size)
    scenes=[Scene(s,cast,size,e['duration'],a['words'],assets,chapter['title']) for s,e,a in zip(chapter['scenes'],entries,audios)]
    def frame(t):
        if t<TITLE_SECONDS:return np.asarray(card)
        for scene,e in zip(scenes,entries):
            if t<e['start']+e['duration']:return np.asarray(scene.frame(t-e['start']))
        return np.asarray(scenes[-1].frame(entries[-1]['duration']))
    clip=VideoClip(frame,duration=total)
    try:clip.write_videofile(str(work/'silent.mp4'),fps=FPS,codec='libx264',audio=False,preset='veryfast',threads=2,logger=None,ffmpeg_params=['-crf','23','-pix_fmt','yuv420p'])
    finally:clip.close()
    events=[{'start':e['start'],'words':a['words']} for e,a in zip(entries,audios)]
    args=['ffmpeg','-y','-v','error','-i','silent.mp4','-i','audio.wav']
    if mode=='shorts' or captions:
        (work/'captions.ass').write_text(build_ass(events,size,karaoke=mode=='shorts'),encoding='utf-8');args+=['-vf','subtitles=captions.ass','-c:v','libx264','-preset','veryfast','-crf','23','-threads','2']
    else:args+=['-c:v','copy']
    args+=['-c:a','aac','-ar','24000','-b:a','96k','-t',str(total),'-movflags','+faststart','chapter.mp4'];command(args,cwd=work)
    write_json(work/'timing.json',{'duration':total,'scenes':entries,'alignment':[a['alignment'] for a in audios]})
    return work/'chapter.mp4',media_duration(work/'chapter.mp4')

def render_story(story,output,voice_name='bm_george',speed=1.0,preview=False,captions=False,smoke=False):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise RuntimeError('Install FFmpeg and ffprobe with libass support')
    if story['mode']=='shorts' and len(story['chapters'])!=1:raise ValueError('Select one Shorts part per render')
    output=output.resolve();output.mkdir(parents=True,exist_ok=True);mode=story['mode']
    size=((540,960) if preview else (1080,1920)) if mode=='shorts' else ((960,540) if preview else (1920,1080))
    voice=Narrator(voice_name,speed);cast={c['id']:c for c in story['outline']['cast']};started=time.monotonic();segments=[];stamps=[];elapsed=0
    assets=str(Path(__file__).resolve().parent/'assets'/'characters')
    for ch in story['chapters']:
        segment,seconds=render_chapter(ch,cast,size,output/f"chapter-{ch['number']:02d}",voice,mode,len(story['outline']['chapters']),assets,captions,smoke)
        stamps.append({'seconds':elapsed,'title':ch['title'],'number':ch['number']});elapsed+=seconds;segments.append(segment)
    (output/'concat.txt').write_text(''.join(f"file '{p.relative_to(output).as_posix()}'\n" for p in segments))
    command(['ffmpeg','-y','-v','error','-f','concat','-safe','1','-i','concat.txt','-c','copy','-movflags','+faststart','video.mp4'],cwd=output)
    elapsed=media_duration(output/'video.mp4');seconds=time.monotonic()-started
    display_title=story['outline']['title']
    if mode=='shorts':
        chapter=story['chapters'][0]
        display_title=(f"Part {chapter['number']}/{len(story['outline']['chapters'])}: "+chapter['title'])[:70]
    metadata={'title':display_title,'duration_seconds':elapsed,'chapters':stamps,'voice':voice_name if not smoke else 'synthetic tone; no speech','word_alignment':'estimated','sources':story.get('sources',[]),'disclosure':story.get('disclosure','Offline renderer test; synthetic tone audio.'),'render_wall_seconds':seconds,'render_seconds_per_video_second':seconds/elapsed,'resolution':size,'duration_in_target':(60<=elapsed<=90 if mode=='shorts' else 480<=elapsed<=900) if not smoke else None}
    metadata['hashtags']=['#History','#Stickman']+(['#Shorts'] if mode=='shorts' else [])
    metadata['tags']=['history','animated history','stickman']
    description=[display_title,story.get('outline',{}).get('logline',''),metadata['disclosure']]
    if mode=='long':
        description+=['','Chapters']+[f"{int(c['seconds'])//60}:{int(c['seconds'])%60:02d} {c['title']}" for c in stamps]
        from thumbnail import create_thumbnail
        create_thumbnail(story['outline']['title'],list(cast.values()),output/'thumbnail.png')
    description+=['','Sources']+[x['title']+' — '+x['url'] for x in story.get('sources',[])]
    description+=['',' '.join(metadata['hashtags'])]
    (output/'description.txt').write_text('\n'.join(description)+'\n',encoding='utf-8');write_json(output/'render-metadata.json',metadata)
    if metadata['duration_in_target'] is False:print('Duration outside target: revise narration or voice speed before publishing.')
    print(f'Rendered {elapsed:.2f}s in {seconds:.2f}s ({seconds/elapsed:.2f}x video duration): {output / "video.mp4"}')
    return output/'video.mp4'

def smoke_story(mode='shorts'):
    cast=[{'id':'demo_ruler','accessory':'crown','accent':'#E0A526'},{'id':'demo_guard','accessory':'spear','accent':'#1FA2A6'}];scenes=[]
    for i,(bg,pose,exp,text) in enumerate(zip(['palace','market','river','battlefield'],['standing','pointing','running','shocked'],['sly','happy','angry','shocked'],['A crown. A plan. What could go wrong?','One bold gesture changes the entire scene.','Across the river, the chase begins.','Then everything stops. This is a visual test.'])):
        scenes.append({'id':f'demo{i}','narration':text,'background':bg,'camera':'push_in','effects':['pop_in','shake'] if i==3 else ['fade'],'props':[['crown'],['coin','scroll'],['torch'],['sword']][i],'emphasis_words':['bold'] if i==1 else [],'characters_on_screen':[{'id':'demo_ruler','x':.3,'pose':pose,'expression':exp},{'id':'demo_guard','x':.7,'pose':'thinking' if i==1 else 'standing','expression':'neutral'}]})
    return {'mode':mode,'outline':{'title':'The crown had a plan','cast':cast,'chapters':[{}]},'chapters':[{'number':1,'title':'The crown had a plan','scenes':scenes}]}
