"""Supersampled modular stick figures with bounded pose cache and PNG overrides."""
from functools import lru_cache
from pathlib import Path
import math
from PIL import Image, ImageDraw
INK='#2B2B2B'; IVORY='#F7F1E3'; TEAL='#1FA2A6'; GOLD='#E0A526'
# Upper/lower arm angles, then upper/lower leg angles, measured from down.
ANGLES={
 'standing':(-18,-8,18,8,-12,-5,12,5), 'talking':(-45,-80,25,65,-12,-5,12,5),
 'pointing':(-15,-10,90,90,-12,-5,12,5),'shocked':(-140,-160,140,160,-22,-10,22,10),
 'running':(-65,-110,65,-20,-45,-90,45,0),'kneeling':(-25,30,25,-30,-75,75,75,-75),
 'fighting':(-80,-140,70,125,-35,-15,35,15),'falling':(-130,-80,120,60,-45,-15,55,15),
 'dead':(-75,-25,75,25,-20,-10,20,10),'laughing':(-55,65,55,-65,-16,-5,16,5),
 'thinking':(-20,-5,135,150,-12,-5,12,5),'praying':(-35,150,35,-150,-12,-5,12,5)}

@lru_cache(maxsize=192)
def character(name,pose,expression,accessory,accent,size=380,blink=False,mouth='closed',assets=''):
    if assets:
        path=Path(assets)/f'{name}_{pose}.png'
        if path.is_file():
            with Image.open(path) as src:
                image=src.convert('RGBA');image.thumbnail((size,size),Image.Resampling.LANCZOS)
            out=Image.new('RGBA',(size,size));out.alpha_composite(image,((size-image.width)//2,size-image.height));return out
    scale=2;im=Image.new('RGBA',(800,800));d=ImageDraw.Draw(im)
    def line(points,fill=INK,width=10):
        pts=[(int(x*scale),int(y*scale)) for x,y in points];d.line(pts,fill=fill,width=width*scale,joint='curve')
        for x,y in pts:
            r=width*scale/2;d.ellipse((x-r,y-r,x+r,y+r),fill=fill)
    def oval(box,fill,outline=None,width=3):d.ellipse(tuple(int(v*scale) for v in box),fill,outline,width*scale)
    def poly(points,fill):d.polygon([(x*scale,y*scale) for x,y in points],fill)
    def limb(p,angles,lengths):
        pts=[p]
        for a,l in zip(angles,lengths):
            p=(p[0]+math.sin(math.radians(a))*l,p[1]+math.cos(math.radians(a))*l);pts.append(p)
        line(pts)
    a=ANGLES[pose];limb((200,180),a[:2],(53,48));limb((200,180),a[2:4],(53,48));line([(200,154),(200,260)])
    limb((200,260),a[4:6],(55,55));limb((200,260),a[6:8],(55,55));oval((141,45,259,163),'white',INK,7)
    if expression=='dead':
        for x in (178,222):line([(x-7,91),(x+7,105)],width=4);line([(x+7,91),(x-7,105)],width=4)
    elif blink:line([(170,99),(184,99)],width=4);line([(216,99),(230,99)],width=4)
    else:
        for x in (177,223):oval((x-5,91,x+5,104),INK)
    brows={'angry':[(167,79,185,86),(215,86,233,79)],'sad':[(167,87,185,80),(215,80,233,87)],'shocked':[(167,73,185,73),(215,73,233,73)],'sly':[(167,80,185,85),(215,76,233,76)]}
    for x,y,xx,yy in brows.get(expression,[(167,80,185,80),(215,80,233,80)]):line([(x,y),(xx,yy)],width=4)
    if mouth in {'open','wide'} or expression=='shocked':
        r=11 if mouth=='wide' or expression=='shocked' else 6;oval((190,119-r,210,122+r),INK)
    elif expression in {'happy','sly'} or mouth=='smile':line([(185,122),(195,130),(205,130),(215,122)],width=4)
    elif expression=='sad' or mouth=='frown':line([(186,130),(200,121),(214,130)],width=4)
    else:line([(190,126),(210,126)],width=4)
    if accessory=='crown':poly([(145,53),(143,15),(172,36),(200,8),(228,36),(257,15),(255,53)],accent)
    elif accessory=='headwrap':poly([(141,77),(148,43),(183,25),(231,35),(259,71)],accent);line([(154,55),(240,63)],IVORY,5)
    elif accessory=='hat':poly([(158,45),(168,15),(232,15),(242,45)],accent);line([(133,48),(267,48)],accent,12)
    elif accessory=='glasses':
        for x in (177,223):oval((x-19,81,x+19,114),None,accent,4)
        line([(196,94),(204,94)],accent,4)
    elif accessory=='beard':poly([(164,126),(200,174),(236,126),(213,143),(187,143)],accent)
    elif accessory=='bow_tie':poly([(178,160),(200,172),(222,160),(222,184),(200,172),(178,184)],accent)
    elif accessory=='sash':line([(187,174),(211,244)],accent,13)
    elif accessory=='spear':line([(290,352),(290,110)],accent,6);poly([(279,119),(290,87),(301,119)],INK)
    if pose in {'dead','falling'}:im=im.rotate(85 if pose=='dead' else 45,resample=Image.Resampling.BICUBIC)
    return im.resize((size,size),Image.Resampling.LANCZOS)
