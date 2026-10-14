"""CPU Kokoro speech with cached WAVs and exact duration measurement."""
from pathlib import Path
import hashlib
import json
import math
import wave
import numpy as np
from captions import word_timings
RATE=24000

def duration(path):
    with wave.open(str(path),'rb') as f:seconds=f.getnframes()/f.getframerate()
    if not math.isfinite(seconds) or seconds<=0:raise ValueError('TTS produced empty audio')
    return seconds

def write_wav(path,audio):
    audio=np.asarray(audio,dtype=np.float32).reshape(-1)
    if not audio.size or not np.isfinite(audio).all():raise ValueError('TTS produced empty or invalid audio')
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp.wav')
    with wave.open(str(tmp),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(RATE);f.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
    tmp.replace(path)

class Narrator:
    def __init__(self,voice='bm_george',speed=1.0):
        if voice not in {'bm_george','bm_fable','am_michael'}:raise ValueError('Unknown narrator voice')
        if not .7<=speed<=1.3:raise ValueError('Speed must be 0.7–1.3')
        self.voice,self.speed,self.pipeline=voice,speed,None
    def synthesize(self,text,cache):
        if not text.strip():raise ValueError('Cannot narrate empty text')
        key=hashlib.sha256(json.dumps([text,self.voice,self.speed,'kokoro-0.9.4']).encode()).hexdigest();path=cache/(key+'.wav')
        if not path.exists():
            if self.pipeline is None:
                try:
                    import torch
                    from kokoro import KPipeline
                except ImportError as exc:raise RuntimeError('Install requirements-voice.txt and espeak-ng for Kokoro') from exc
                torch.set_num_threads(2);self.pipeline=KPipeline(lang_code=self.voice[0],device='cpu')
            chunks=[]
            for _,_,audio in self.pipeline(text,voice=self.voice,speed=self.speed):
                if audio is not None:
                    if hasattr(audio,'detach'):audio=audio.detach().cpu().numpy()
                    chunks.append(np.asarray(audio))
            if not chunks:raise ValueError('Kokoro returned no audio')
            write_wav(path,np.concatenate(chunks))
        seconds=duration(path)
        return {'path':str(path.resolve()),'duration':seconds,'words':word_timings(text,seconds),'alignment':'estimated_from_measured_audio'}

def tone(path,seconds):
    t=np.arange(round(seconds*RATE))/RATE
    write_wav(path,.035*np.sin(2*np.pi*220*t)*np.minimum(1,t*10)*np.minimum(1,(seconds-t)*10))
