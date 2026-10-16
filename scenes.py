"""Procedural backdrops, title cards and deterministic inexpensive animation."""
from functools import lru_cache
from pathlib import Path
import math
import random
from PIL import Image,ImageDraw,ImageFont
from characters import character,INK,IVORY,TEAL,GOLD
@lru_cache(maxsize=32)
def font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','C:/Windows/Fonts/arialbd.ttf'):
        if Path(path).exists():return ImageFont.truetype(path,size)
    raise RuntimeError('Install fonts-dejavu-core or Windows Arial Bold')

def _asset_backdrop(kind, size):
    """Load a matching background plate without moving or modifying source assets.

    Prefer explicit plates under assets/backgrounds/, then inspect direct image files
    in assets/ (the supplied scene plates currently live there). Never scan character
    subfolders or treat prop illustrations as full-frame backgrounds.
    """
    root=Path(__file__).resolve().parent/'assets'
    roots=[root/'backgrounds',root]
    aliases={
        'palace':('palace','royal court','throne room'),
        'throne':('throne room','throne','palace'),
        'room':('room','interior','studio'),
        'river':('river','caravan','desert'),
        'hut':('village','hut'),
        'market':('market','cairo'),
        'street':('street','market','village'),
        'battlefield':('battlefield','desert','caravan'),
        'map':('map',),
        'desert':('desert','sahara','caravan'),
        'forest':('forest',),
        'ship':('ship','deck'),
        'classroom':('classroom',),
        'tavern':('tavern',),
        'studio':('studio',),
    }
    terms=aliases.get(str(kind).lower(),(str(kind).lower(),))
    candidates=[]
    for folder in roots:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
                continue
            stem=path.stem.lower().replace('_',' ').replace('-',' ')
            # Root assets also contain complete storyboard stills; those are not
            # backgrounds and would duplicate characters when used as scene plates.
            if folder.name!='backgrounds' and not any(token in stem for token in
                    ('background','background plate','interior','courtyard','sand dunes','forest path','ship deck')):
                continue
            score=max((len(term) for term in terms if term in stem),default=0)
            if score:
                candidates.append((score,0 if folder.name=='backgrounds' else 1,path.name.lower(),path))
    if not candidates:
        return None
    try:
        _,_,_,chosen=sorted(candidates,key=lambda row:(-row[0],row[1],row[2]))[0]
        with Image.open(chosen) as source:
            image=source.convert('RGB')
        target_ratio=size[0]/size[1]
        ratio=image.width/image.height
        if ratio>target_ratio:
            width=max(1,round(image.height*target_ratio))
            left=(image.width-width)//2
            image=image.crop((left,0,left+width,image.height))
        elif ratio<target_ratio:
            height=max(1,round(image.width/target_ratio))
            top=(image.height-height)//2
            image=image.crop((0,top,image.width,top+height))
        return image.resize(size,Image.Resampling.LANCZOS)
    except (OSError,ValueError):
        return None


def centered_text(im,text,y,max_width,fontsize,color=INK):
    d=ImageDraw.Draw(im);f=font(fontsize);lines=[];line=''
    for word in text.split():
        candidate=(line+' '+word).strip()
        if d.textlength(candidate,font=f)>max_width and line:lines.append(line);line=word
        else:line=candidate
    if line:lines.append(line)
    for line in lines:
        while d.textlength(line,font=f)>max_width and f.size>12:f=font(f.size-2)
        d.text((im.width/2,y),line,font=f,fill=color,anchor='mt');y+=f.size*1.3
    return y

@lru_cache(maxsize=24)
def backdrop(kind,size):
    asset=_asset_backdrop(kind,size)
    if asset is not None:
        return asset
    w,h=size;im=Image.new('RGB',size,IVORY);d=ImageDraw.Draw(im)
    def box(x,y,xx,yy,c):d.rectangle((x*w,y*h,xx*w,yy*h),fill=c)
    def poly(points,c):d.polygon([(x*w,y*h) for x,y in points],fill=c)
    box(0,.72,1,1,'#EAE0CA')
    if kind=='river':poly([(0,.65),(.4,.66),(.7,1),(.25,1)],TEAL)
    elif kind in {'palace','throne','room'}:
        box(.08,.18,.92,.72,'#E7D8B9')
        for x in (.1,.32,.64,.86):box(x,.18,x+.035,.72,IVORY)
        if kind=='throne':box(.4,.42,.6,.72,GOLD);box(.43,.32,.57,.59,GOLD)
        else:box(.43,.36,.57,.72,INK)
    elif kind in {'hut','market','street'}:
        for x in (.04,.66):
            box(x,.48,x+.28,.73,'#D8BF91');poly([(x-.02,.48),(x+.14,.32),(x+.3,.48)],GOLD);box(x+.10,.57,x+.18,.73,INK)
        if kind=='market':box(.38,.5,.62,.54,TEAL);box(.4,.54,.42,.72,INK);box(.58,.54,.6,.72,INK)
    elif kind=='battlefield':
        for x in (.06,.16,.84,.94):d.line((x*w,.3*h,x*w,.73*h),fill=INK,width=max(2,w//250));poly([(x,.3),(x+.06,.34),(x,.38)],'#C0392B')
    elif kind=='map':poly([(.2,.25),(.5,.2),(.8,.4),(.66,.65),(.4,.7),(.18,.48)],'#D8BF91');d.line((.28*w,.4*h,.6*w,.55*h),fill=TEAL,width=max(2,w//150))
    return im

@lru_cache(maxsize=32)
def _prop_asset(kind, size):
    """Load a keyed local prop illustration, or return None for procedural fallback."""
    root=Path(__file__).resolve().parent/'assets'
    aliases={
        'crown':('crown illustration','golden crown'),
        'scroll':('parchment scroll','scroll prop'),
        'sword':('sword 2d cartoon','sword prop'),
        'map':('hand drawn map','map on parchment'),
        'table':('wooden table','table prop'),
        'chest':('treasure chest','wooden chest'),
    }
    terms=aliases.get(str(kind).lower(),())
    candidates=[]
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
                continue
            stem=path.stem.lower().replace('_',' ').replace('-',' ')
            score=max((len(term) for term in terms if term in stem),default=0)
            if score:candidates.append((score,path.name.lower(),path))
    if not candidates:return None
    try:
        _,_,chosen=sorted(candidates,key=lambda row:(-row[0],row[1]))[0]
        with Image.open(chosen) as source:image=source.convert('RGBA')
        px=image.load()
        for yy in range(image.height):
            for xx in range(image.width):
                r,g,b,alpha=px[xx,yy]
                if r>=235 and b>=235 and g<=45:
                    px[xx,yy]=(r,g,b,0)
                elif r>200 and b>200 and g<90 and abs(r-b)<55:
                    px[xx,yy]=(r,g,b,0)
        image.thumbnail((size,size),Image.Resampling.LANCZOS)
        return image
    except (OSError,ValueError):
        return None


def prop_icon(im,kind,x,y,s):
    d=ImageDraw.Draw(im);x,y,s=int(x),int(y),int(s)
    asset=_prop_asset(kind,max(1,s*4))
    if asset is not None:
        im.paste(asset,(x-asset.width//2,y-asset.height//2),asset)
        return
    if kind=='coin':d.ellipse((x-s,y-s,x+s,y+s),fill=GOLD,outline=INK,width=2)
    elif kind=='scroll':d.rounded_rectangle((x-s,y-s,x+s,y+s),radius=4,fill=IVORY,outline=INK,width=3);d.line((x-s/2,y,x+s/2,y),fill=INK,width=2)
    elif kind=='crown':d.polygon([(x-s,y+s),(x-s,y-s),(x,y),(x+s,y-s),(x+s,y+s)],fill=GOLD)
    elif kind in {'fire','torch'}:
        if kind=='torch':d.line((x,y,x,y+s*2),fill=INK,width=5)
        d.polygon([(x-s,y),(x,y-s*2),(x+s,y),(x,y+s)],fill='#C0392B');d.ellipse((x-s/2,y-s/2,x+s/2,y+s/2),fill=GOLD)
    else:
        d.line((x,y+s*2,x,y-s),fill=INK,width=5);d.polygon([(x-s/2,y-s),(x,y-s*2),(x+s/2,y-s)],fill=TEAL)
        if kind=='sword':d.line((x-s,y+s/2,x+s,y+s/2),fill=GOLD,width=5)

class Scene:
    def __init__(self,data,cast,size,duration,words,assets='',heading=''):
        self.data,self.cast,self.size,self.duration,self.words,self.assets,self.heading=data,cast,size,duration,words,assets,heading;self.blinks={}
        for actor in data['characters_on_screen']:
            rng=random.Random(data['id']+actor['id']);times=[];t=rng.uniform(2,4)
            while t<duration:times.append(t);t+=rng.uniform(2,5)
            self.blinks[actor['id']]=times
    def frame(self,t):
        t=math.floor(t*12)/12;w,h=self.size;d=self.data;im=backdrop(d['background'],self.size).copy().convert('RGBA');count=len(d['characters_on_screen']);s=int(min(h*.49,w*(.9 if count<2 else .75 if count==2 else .48)))
        for i,a in enumerate(d['characters_on_screen']):
            c=self.cast[a['id']];blink=any(0<=t-b<.17 for b in self.blinks[a['id']])
            emphasis=any(q['start']<=t<q['end'] and any(e.casefold() in q['word'].casefold() for e in d['emphasis_words']) for q in self.words)
            pose='pointing' if emphasis and a['pose'] in {'standing','talking'} else a['pose']
            mouth='open' if a['pose']=='talking' and int(t*8)%2 and any(q['start']<=t<q['end'] for q in self.words) else 'closed'
            sprite=character(a['id'],pose,a['expression'],c['accessory'],c['accent'],s,blink,mouth,self.assets);x=a['x']*w-s/2;y=h*.75-s
            if a['pose']=='running':x+=math.sin(t*1.6)*w*.06;y-=abs(math.sin(t*8))*s*.025
            elif a['pose'] not in {'dead','falling'}:y+=math.sin(t*3+i)*s*.008
            if 'pop_in' in d['effects'] and t<.35:y+=(1-t/.35)**2*h*.3
            x=max(-s*.25,min(w-s*.75,x));im.alpha_composite(sprite,(int(x),int(y)))
        for i,p in enumerate(d['props']):prop_icon(im,p,w*(.35+i*.12),h*.64,w*.022)
        im=im.convert('RGB');camera=d['camera'];progress=min(1,t/max(.01,self.duration));zoom=1+.045*progress if camera=='push_in' else 1.025 if camera.startswith('pan') else 1
        if zoom>1:
            cw,ch=int(w/zoom),int(h/zoom);x=(w-cw)/2;y=(h-ch)/2
            if camera=='pan_left':x=(w-cw)*progress
            if camera=='pan_right':x=(w-cw)*(1-progress)
            im=im.crop((int(x),int(y),int(x)+cw,int(y)+ch)).resize(self.size,Image.Resampling.BILINEAR)
        if 'shake' in d['effects'] and t<.65:
            canvas=Image.new('RGB',self.size,IVORY);canvas.paste(im,(round(math.sin(t*70)*w*.006),round(math.cos(t*85)*h*.004)));im=canvas
        if 'fade' in d['effects'] and t<.25:im=Image.blend(Image.new('RGB',self.size,IVORY),im,t/.25)
        if self.heading:
            d=ImageDraw.Draw(im)
            top, bottom=int(h*.025),int(h*.125)
            d.rounded_rectangle((int(w*.035),top,int(w*.965),bottom),
                                radius=max(8,int(min(w,h)*.012)),fill=IVORY,
                                outline=GOLD,width=max(2,int(min(w,h)*.002)))
            centered_text(im,self.heading,h*.045,w*.84,max(18,int(w*(.055 if h>w else .028))))
        return im

def title_card(title,label,size):
    im=Image.new('RGB',size,IVORY);w,h=size;centered_text(im,label,h*.25,w*.85,max(16,int(w*.035)),TEAL);centered_text(im,title,h*.38,w*.85,max(25,int(w*.065)))
    ImageDraw.Draw(im).rectangle((w*.42,h*.7,w*.58,h*.71),fill=GOLD);return im
