"""ASS karaoke. Word alignment is estimated from measured narration length."""
import math
import re

def word_timings(text,duration):
    tokens=text.split()
    if not tokens or not math.isfinite(duration) or duration<=0:raise ValueError('Empty text or invalid duration')
    weights=[max(1,len(re.sub(r'\W','',t))) for t in tokens];total=sum(weights);cursor=0;result=[]
    for token,weight in zip(tokens,weights):
        end=cursor+duration*weight/total;result.append({'word':token,'start':cursor,'end':end});cursor=end
    result[-1]['end']=duration;return result

def stamp(t):
    n=max(0,round(t*100));h,n=divmod(n,360000);m,n=divmod(n,6000);s,c=divmod(n,100)
    return f'{h}:{m:02d}:{s:02d}.{c:02d}'

def escape(text):return text.replace('\\','／').replace('{','(').replace('}',')').replace('\n',' ')

def build_ass(events,size,karaoke=True):
    w,h=size;fs=round(w*(.052 if h>w else .033));margin=round(h*.18)
    header=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{fs},&H00A6A21F,&H002B2B2B,&H00E3F1F7,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,40,40,{margin},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[]
    for event in events:
        groups=[];group=[];length=0
        for word in event['words']:
            if group and (length+len(word['word'])>25 or len(group)>=5):groups.append(group);group=[];length=0
            group.append(word);length+=len(word['word'])+1
        if group:groups.append(group)
        for group in groups:
            content=' '.join((f"{{\\k{max(1,round((a['end']-a['start'])*100))}}}" if karaoke else '')+escape(a['word']) for a in group)
            offset=event['start'];lines.append(f"Dialogue: 0,{stamp(offset+group[0]['start'])},{stamp(offset+group[-1]['end'])},Default,,0,0,0,,{content}")
    return header+'\n'.join(lines)+'\n'
