"""Reference-matched illustration and layout overrides for animation_engine.

Only presentation code lives here. No script generation, networking, research, or delivery.
"""
from __future__ import annotations

import math
import random
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

import animation_engine as e

INK = (43, 39, 35)
PAPER = (246, 238, 216)
MUSTARD = (238, 194, 92)


def _line(d, pts, color=INK, width=4):
    d.line(pts, fill=color, width=max(2, int(width)), joint="curve")


def _person(d, x, foot, scale, coat=(111, 75, 49), pose="standing", hood=False):
    """Small costumed medieval figure, sized for landscape illustration plates."""
    s = scale
    r = s * .085
    hy = foot - s * .80
    d.ellipse((x-r,hy-r,x+r,hy+r),fill=PAPER,outline=INK,width=max(2,int(s*.018)))
    if hood:
        d.polygon([(x-r*1.25,hy+r*.02),(x,hy-r*1.52),(x+r*1.25,hy+r*.02)],
                  fill=coat,outline=INK)
    else:
        d.arc((x-r,hy-r*1.2,x+r,hy+r*.45),180,355,fill=(83,55,38),width=max(2,int(s*.025)))
    eye=max(2,int(s*.008))
    for ex in (x-r*.32,x+r*.18):
        d.ellipse((ex,hy-s*.012,ex+eye,hy-s*.012+eye),fill=INK)
    d.arc((x-r*.22,hy+r*.12,x+r*.22,hy+r*.43),0,180,fill=INK,width=max(1,int(s*.009)))
    shoulder=foot-s*.55;hip=foot-s*.27
    d.polygon([(x-s*.10,shoulder),(x+s*.10,shoulder),(x+s*.085,hip),(x-s*.085,hip)],
              fill=coat,outline=INK)
    _line(d,[(x,hy+r*.9),(x,hip)],INK,max(2,int(s*.015)))
    swing=math.sin(scale*.013)*s*.03 if pose in {"run","running","chase"} else 0
    _line(d,[(x-s*.08,shoulder+s*.07),(x-s*.16+swing,foot-s*.34)],INK,max(2,int(s*.017)))
    _line(d,[(x+s*.08,shoulder+s*.07),(x+s*.16-swing,foot-s*.34)],INK,max(2,int(s*.017)))
    if pose in {"run","running","chase"}:
        _line(d,[(x-s*.025,hip),(x-s*.14,foot-s*.11),(x-s*.22,foot-s*.07)],INK,max(2,int(s*.018)))
        _line(d,[(x+s*.025,hip),(x+s*.13,foot-s*.19),(x+s*.22,foot-s*.15)],INK,max(2,int(s*.018)))
    else:
        _line(d,[(x-s*.025,hip),(x-s*.04,foot-s*.02)],INK,max(2,int(s*.018)))
        _line(d,[(x+s*.025,hip),(x+s*.07,foot-s*.02)],INK,max(2,int(s*.018)))
    d.ellipse((x-s*.09,foot-s*.04,x-s*.012,foot+s*.004),fill=INK)
    d.ellipse((x+s*.02,foot-s*.04,x+s*.10,foot+s*.004),fill=INK)


def _cow(d, x, ground, s, lying=False):
    cream=(246,239,219); dark=(75,68,60)
    if lying:
        d.ellipse((x-s*.5,ground-s*.42,x+s*.37,ground-s*.08),fill=cream,outline=INK,width=max(3,int(s*.025)))
        d.ellipse((x-s*.28,ground-s*.39,x-s*.10,ground-s*.20),fill=dark)
        d.ellipse((x+s*.05,ground-s*.36,x+s*.24,ground-s*.18),fill=dark)
        hx=x+s*.39;hy=ground-s*.32
        d.ellipse((hx-s*.13,hy-s*.14,hx+s*.12,hy+s*.08),fill=cream,outline=INK,width=max(3,int(s*.02)))
        d.ellipse((hx+s*.035,hy-s*.025,hx+s*.065,hy+.005*s),fill=INK)
        _line(d,[(x-s*.26,ground-s*.12),(x-s*.23,ground)],INK,max(3,int(s*.025)))
        _line(d,[(x+s*.13,ground-s*.12),(x+s*.17,ground)],INK,max(3,int(s*.025)))
    else:
        d.ellipse((x-s*.42,ground-s*.65,x+s*.33,ground-s*.12),fill=cream,outline=INK,width=max(3,int(s*.023)))
        d.ellipse((x-s*.20,ground-s*.57,x+s*.02,ground-s*.34),fill=dark)
        d.ellipse((x+s*.08,ground-s*.53,x+s*.28,ground-s*.33),fill=dark)
        hx=x+s*.39;hy=ground-s*.47
        d.ellipse((hx-s*.11,hy-s*.17,hx+s*.16,hy+s*.08),fill=cream,outline=INK,width=max(3,int(s*.022)))
        d.polygon([(hx-s*.03,hy-s*.13),(hx-s*.11,hy-s*.26),(hx+s*.005,hy-s*.15)],fill=cream,outline=INK)
        d.polygon([(hx+s*.10,hy-s*.13),(hx+s*.18,hy-s*.25),(hx+s*.17,hy-s*.10)],fill=cream,outline=INK)
        d.ellipse((hx+s*.045,hy-s*.035,hx+s*.075,hy-.005*s),fill=INK)
        d.ellipse((hx+s*.06,hy+s*.015,hx+s*.14,hy+s*.06),fill=(220,164,145),outline=INK,width=2)
        for lx in (x-s*.28,x-s*.02,x+s*.20):
            _line(d,[(lx,ground-s*.18),(lx-s*.01,ground-s*.015)],INK,max(3,int(s*.025)))
            d.line((lx-s*.035,ground,lx+s*.025,ground),fill=INK,width=max(3,int(s*.025)))
        _line(d,[(x-s*.38,ground-s*.45),(x-s*.51,ground-s*.28),(x-s*.48,ground-s*.20)],INK,max(2,int(s*.018)))


def _cauldron(d, cx, cy, r, fire=False):
    d.arc((cx-r*.70,cy-r*.72,cx+r*.70,cy+r*.63),180,360,fill=INK,width=max(4,int(r*.07)))
    d.ellipse((cx-r*.65,cy-r*.18,cx+r*.65,cy+r*.62),fill=(53,51,49),outline=INK,width=max(4,int(r*.055)))
    d.ellipse((cx-r*.61,cy-r*.28,cx+r*.61,cy+.02*r),fill=(125,95,57),outline=INK,width=max(3,int(r*.035)))
    for i in range(7):
        xx=cx-r*.45+i*r*.15;yy=cy-r*.12+math.sin(i*2.3)*r*.04
        d.ellipse((xx-r*.045,yy-r*.035,xx+r*.045,yy+r*.035),fill=(214,177,108),outline=INK,width=1)
    _line(d,[(cx-r*.34,cy+r*.48),(cx-r*.45,cy+r*.84)],INK,max(4,int(r*.08)))
    _line(d,[(cx+r*.34,cy+r*.48),(cx+r*.45,cy+r*.84)],INK,max(4,int(r*.08)))
    if fire:
        for off in (-.25,0,.25):
            fx=cx+off*r*.9;fy=cy+r*.7
            d.polygon([(fx-r*.12,fy),(fx,fy-r*(.46+(off==0)*.18)),(fx+r*.12,fy)],
                      fill=(194,67,36),outline=INK)
            d.polygon([(fx-r*.055,fy),(fx,fy-r*.27),(fx+r*.06,fy)],fill=(246,183,56))


def _illustration_plate(base: Image.Image, shot: dict[str,Any]) -> Image.Image:
    """Paint distinct story scenes over a procedural base with layered period detail."""
    im=base.convert("RGB").copy();d=ImageDraw.Draw(im);w,h=im.size
    theme=str(shot.get("theme","generic"));key=(str(shot.get("scene",""))+" "+str(shot.get("id",""))).lower()
    rng=random.Random(e._seed(str(shot.get("id","plate"))))
    if theme=="snow_forest" or "snow" in key or "winter" in key:
        d.rectangle((0,h*.63,w,h),fill=(225,231,226))
        for tx,top in ((65,.24),(220,.17),(1020,.21),(1195,.29),(890,.33)):
            base_y=h*.77;top_y=h*top
            _line(d,[(tx,base_y),(tx,top_y)],(89,77,62),max(4,int(w*.005)))
            for j in range(5):
                yy=top_y+(base_y-top_y)*(j+1)/6;reach=(base_y-yy)*.36
                _line(d,[(tx,yy),(tx-reach,yy-reach*.42)],(89,77,62),max(3,int(w*.003)))
                _line(d,[(tx,yy),(tx+reach,yy-reach*.42)],(89,77,62),max(3,int(w*.003)))
                d.line((tx-reach,yy-reach*.42,tx-reach*.48,yy-reach*.55),fill=(246,248,242),width=max(3,int(w*.004)))
                d.line((tx+reach,yy-reach*.42,tx+reach*.48,yy-reach*.55),fill=(246,248,242),width=max(3,int(w*.004)))
        for i in range(50):
            x=rng.randrange(w);y=rng.randint(int(h*.66),h-1)
            d.ellipse((x,y,x+rng.randint(2,8),y+rng.randint(1,4)),fill=(191,208,207))
        _person(d,w*.49,h*.91,h*.48,coat=(110,72,50),pose="standing",hood=True)
        for i in range(9):
            x=w*(.30+i*.018);y=h*(.91-i*.026)
            d.ellipse((x,y,x+w*.008,y+h*.016),fill=(170,189,193))
    elif theme in {"village_street","market"} or "street" in key or "village" in key or "cart" in key or "wagon" in key:
        for bx,bw,top,bottom in ((25,350,.18,.77),(905,350,.12,.78)):
            x0=bx;y0=h*top;x1=bx+bw;y1=h*bottom
            d.rectangle((x0,y0,x1,y1),fill=(204,177,139),outline=INK,width=5)
            d.polygon([(x0-22,y0+14),(x0+bw*.5,y0-h*.18),(x1+22,y0+14)],fill=(93,62,47),outline=INK)
            for row in range(8):
                yy=y0+row*(y1-y0)/8;d.line((x0,yy,x1,yy+2),fill=(157,123,90),width=2)
            for col in (.18,.50,.82):
                wx=x0+bw*col;wy=y0+(y1-y0)*.24
                d.rectangle((wx-29,wy-24,wx+29,wy+42),fill=(93,125,132),outline=INK,width=4)
                d.line((wx,wy-22,wx,wy+40),fill=PAPER,width=4);d.line((wx-27,wy+8,wx+27,wy+8),fill=PAPER,width=4)
            for bx0,by0,bx1,by1 in ((x0,y0,x1,y0),(x0+bw*.33,y0,x0+bw*.33,y1),
                                    (x0+bw*.68,y0,x0+bw*.68,y1),(x0,y0+(y1-y0)*.52,x1,y0+(y1-y0)*.52)):
                _line(d,[(bx0,by0),(bx1,by1)],(83,56,42),max(5,int(w*.006)))
            for row in range(4):
                for col in range(7):
                    xx=x0+col*bw/7+(row%2)*12;yy=y0+(y1-y0)*.58+row*17
                    d.arc((xx,yy,xx+27,yy+15),180,350,fill=(126,94,69),width=2)
        d.polygon([(w*.37,h*.54),(w*.62,h*.54),(w*.96,h),(w*.06,h)],fill=(176,157,129))
        for row in range(6):
            yy=h*(.60+row*.065)
            for col in range(10):
                xx=w*(.05+col*.10+(row%2)*.025)
                d.arc((xx,yy,xx+w*.085,yy+h*.038),180,350,fill=(126,111,93),width=2)
        if "cart" in key or "wagon" in key:
            d.polygon([(w*.38,h*.59),(w*.62,h*.59),(w*.68,h*.76),(w*.34,h*.76)],fill=(132,84,49),outline=INK)
            for i in range(5):
                xx=w*(.39+i*.045);d.line((xx,h*.60,xx+w*.02,h*.75),fill=(186,135,81),width=4)
            for xx in (w*.39,w*.62):
                d.ellipse((xx-w*.045,h*.71,xx+w*.045,h*.88),fill=(62,50,41),outline=INK,width=6)
                d.ellipse((xx-w*.018,h*.74,xx+w*.018,h*.85),fill=(164,135,98),outline=INK,width=3)
            _person(d,w*.74,h*.88,h*.42,coat=(77,96,103),pose="running")
        else:
            _person(d,w*.43,h*.89,h*.37,coat=(105,68,49),pose="standing")
            _person(d,w*.64,h*.90,h*.41,coat=(76,92,71),pose="standing",hood=True)
        d.line((w*.17,h*.37,w*.17,h*.52),fill=INK,width=5)
        d.rectangle((w*.145,h*.38,w*.195,h*.45),fill=(233,178,77),outline=INK,width=3)
        for j in range(3):
            d.arc((w*.79+j*24,h*.12-j*17,w*.84+j*24,h*.23-j*17),180,350,fill=(108,101,92),width=3)
    elif theme=="barn" or "cow" in key or "barn" in key or "animal" in key:
        d.rectangle((0,0,w,h),fill=(157,104,74))
        for y in range(0,h,48):
            d.line((0,y,w,y),fill=(127,78,55),width=3)
            for x in range(rng.randint(15,40),w,170):
                d.ellipse((x,y+10,x+5,y+15),fill=(80,53,41))
        for bx in (0,w*.25,w*.5,w*.75,w-28):
            d.polygon([(bx,0),(bx+18,0),(bx+4,h*.87),(bx-12,h*.87)],fill=(94,58,43),outline=INK)
        for off in (0,.15,.30):
            _line(d,[(w*.08,h*.03),(w*.5,h*(.42+off)),(w*.92,h*.03)],(77,51,41),max(5,int(w*.006)))
        d.rectangle((0,h*.76,w,h),fill=(174,136,83))
        for i in range(90):
            x=rng.randint(0,w);y=rng.randint(int(h*.72),h-1)
            d.line((x,y,x+rng.randint(-18,18),y-rng.randint(3,14)),fill=(222,182,110),width=2)
        _cow(d,w*.34,h*.82,h*.55,lying=("bed" in key or "sleep" in key))
        _cow(d,w*.74,h*.85,h*.46)
        if "fume" in key or "smell" in key:
            _person(d,w*.52,h*.87,h*.45,coat=(112,75,52),pose="standing",hood=True)
        d.rectangle((w*.04,h*.67,w*.96,h*.71),fill=(102,65,45),outline=INK,width=4)
        d.line((w*.04,h*.69,w*.96,h*.69),fill=(198,146,87),width=3)
    elif theme in {"kitchen","winter_room"} or any(x in key for x in ("pot","hearth","cook","room","bed","porridge","pottage")):
        d.rectangle((0,0,w,h),fill=(190,157,119))
        for yy in range(0,h,47):
            d.line((0,yy,w,yy),fill=(163,126,92),width=2)
            for xx in range(rng.randint(10,80),w,155):
                d.arc((xx,yy+4,xx+32,yy+20),180,350,fill=(137,101,76),width=2)
        for bx in (w*.07,w*.93):
            d.polygon([(bx-18,0),(bx+20,0),(bx+10,h),(bx-30,h)],fill=(92,61,45),outline=INK)
        hx,hy=w*.12,h*.18
        d.rectangle((hx,hy,hx+w*.27,h*.80),fill=(113,101,84),outline=INK,width=5)
        for row in range(7):
            yy=hy+row*31
            for col in range(5):
                xx=hx+col*58+(row%2)*22
                d.rectangle((xx,yy,xx+54,yy+27),outline=(73,67,59),width=2)
        d.arc((hx+w*.055,h*.42,hx+w*.215,h*.77),180,360,fill=(52,42,34),width=6)
        d.rectangle((hx+w*.065,h*.60,hx+w*.205,h*.78),fill=(52,43,35))
        for k in range(3):
            fx=hx+w*(.10+k*.04);fy=h*.74
            d.polygon([(fx-w*.024,fy),(fx,fy-h*(.12+.025*(k%2))),(fx+w*.024,fy)],fill=(196,71,38),outline=INK)
            d.polygon([(fx-w*.012,fy),(fx,fy-h*.075),(fx+w*.012,fy)],fill=(244,179,66))
        _cauldron(d,w*.52,h*.55,h*.20,False)
        for k in range(4):
            px=w*(.48+k*.04);py=h*(.25-(k%2)*.035)
            d.arc((px-20,py-35,px+20,py+5),190,350,fill=(244,241,225),width=5)
        d.polygon([(w*.57,h*.68),(w*.91,h*.68),(w*.96,h*.77),(w*.53,h*.77)],fill=(121,79,48),outline=INK)
        for xx in (w*.59,w*.90):_line(d,[(xx,h*.75),(xx-8,h*.96)],INK,7)
        d.ellipse((w*.68,h*.63,w*.79,h*.69),fill=(208,173,113),outline=INK,width=3)
        d.ellipse((w*.82,h*.64,w*.88,h*.68),fill=(214,192,150),outline=INK,width=3)
        if "sleep" in key or "bed" in key:
            d.rectangle((w*.48,h*.48,w*.93,h*.58),fill=(110,75,50),outline=INK,width=4)
            d.polygon([(w*.48,h*.50),(w*.94,h*.50),(w*.90,h*.39),(w*.52,h*.39)],fill=(226,214,185),outline=INK)
            _person(d,w*.71,h*.58,h*.34,coat=(114,89,68))
        else:
            _person(d,w*.80,h*.90,h*.44,coat=(147,83,53),hood=True)
    else:
        # Open pasture with a rough fence and grazing cattle rather than an empty flat plate.
        d.rectangle((0,h*.58,w,h),fill=(150,162,109))
        for i in range(8):
            x=w*(.04+i*.13);d.line((x,h*.56,x,h*.82),fill=(93,67,47),width=5)
        d.line((w*.02,h*.65,w*.99,h*.65),fill=(103,75,53),width=5)
        d.line((w*.02,h*.75,w*.99,h*.75),fill=(103,75,53),width=5)
        _cow(d,w*.34,h*.84,h*.46);_cow(d,w*.69,h*.84,h*.40)
        _person(d,w*.51,h*.89,h*.42,coat=(107,74,52))
    # Scattered short marks imitate ink/paper texture without adding visible noise shimmer.
    for _ in range(210):
        x=rng.randrange(w);y=rng.randrange(h)
        if rng.random()<.5:
            old=im.getpixel((x,y));inc=rng.randint(2,8)
            d.point((x,y),fill=tuple(min(255,c+inc) for c in old))
    return im


def _icon(d, name, center, r):
    x,y,r=center; ink=(31,29,27); paper=(249,241,216); sw=max(4,int(r*.055))
    if name in {"cow","cattle"}:
        d.ellipse((x-r*.62,y-r*.24,x+r*.42,y+r*.35),fill=paper,outline=ink,width=sw)
        d.ellipse((x-r*.17,y-r*.17,x+r*.04,y+r*.10),fill=(78,70,62))
        d.ellipse((x+r*.08,y-r*.09,x+r*.28,y+r*.13),fill=(78,70,62))
        d.ellipse((x+r*.34,y-r*.30,x+r*.66,y+r*.12),fill=paper,outline=ink,width=sw)
        d.polygon([(x+r*.39,y-r*.27),(x+r*.30,y-r*.47),(x+r*.48,y-r*.32)],fill=paper,outline=ink)
        d.polygon([(x+r*.55,y-r*.27),(x+r*.67,y-r*.47),(x+r*.64,y-r*.22)],fill=paper,outline=ink)
        d.ellipse((x+r*.48,y-r*.12,x+r*.52,y-r*.08),fill=ink)
        d.ellipse((x+r*.52,y-r*.015,x+r*.64,y+r*.07),fill=(223,165,143),outline=ink,width=2)
        for lx in (x-r*.42,x-r*.12,x+r*.16):
            d.line((lx,y+r*.20,lx,y+r*.55),fill=ink,width=sw)
        d.line((x-r*.60,y-r*.05,x-r*.77,y-r*.28,x-r*.73,y-r*.42),fill=ink,width=max(3,sw//2))
    elif name in {"dog","pet"}:
        d.ellipse((x-r*.52,y-r*.22,x+r*.50,y+r*.42),fill=paper,outline=ink,width=sw)
        d.ellipse((x-r*.47,y-r*.50,x+r*.30,y+r*.10),fill=paper,outline=ink,width=sw)
        d.polygon([(x-r*.36,y-r*.34),(x-r*.76,y-r*.55),(x-r*.62,y-r*.03)],fill=(105,72,51),outline=ink)
        d.polygon([(x+r*.14,y-r*.37),(x+r*.58,y-r*.56),(x+r*.45,y-r*.04)],fill=(105,72,51),outline=ink)
        for ex in (x-r*.25,x+r*.03):d.ellipse((ex,y-r*.24,ex+r*.08,y-r*.15),fill=ink)
        d.ellipse((x-r*.13,y-r*.03,x+r*.02,y+r*.07),fill=ink)
        for lx in (x-r*.32,x-r*.03,x+r*.24):d.line((lx,y+r*.28,lx,y+r*.52),fill=ink,width=sw)
    elif name in {"pot","cauldron","pottage"}:
        _cauldron(d,x,y-r*.04,r*.62,False)
        for off in (-.22,0,.22):d.arc((x+off*r-r*.08,y-r*.84,x+off*r+r*.13,y-r*.42),190,350,fill=paper,width=max(4,sw))
    elif name in {"bread","loaf"}:
        d.ellipse((x-r*.61,y+r*.18,x+r*.61,y+r*.48),fill=(128,86,52),outline=ink,width=sw)
        d.rounded_rectangle((x-r*.48,y-r*.20,x+r*.48,y+r*.28),radius=int(r*.23),fill=(214,160,82),outline=ink,width=sw)
        for off in (-.22,0,.22):d.arc((x+r*(off-.13),y-r*.17,x+r*(off+.13),y+r*.16),195,340,fill=(248,218,157),width=max(3,sw//2))
    elif name in {"fire","flame"}:
        d.polygon([(x-r*.48,y+r*.46),(x-r*.42,y-r*.05),(x-r*.10,y+r*.10),(x+r*.02,y-r*.57),(x+r*.28,y-r*.08),(x+r*.45,y+r*.05),(x+r*.48,y+r*.46)],fill=(204,73,37),outline=ink)
        d.polygon([(x-r*.19,y+r*.44),(x-r*.08,y+r*.05),(x+r*.05,y+r*.18),(x+r*.18,y-r*.12),(x+r*.29,y+r*.45)],fill=(246,184,64))
    elif name in {"radiator","heater"}:
        d.rounded_rectangle((x-r*.65,y-r*.42,x+r*.65,y+r*.43),radius=int(r*.08),fill=paper,outline=ink,width=sw)
        for i in range(5):
            xx=x-r*.50+i*r*.22;d.rounded_rectangle((xx,y-r*.31,xx+r*.12,y+r*.30),radius=int(r*.04),fill=(204,200,184),outline=ink,width=max(2,sw//2))
    elif name=="stove":
        d.rectangle((x-r*.50,y-r*.31,x+r*.50,y+r*.47),fill=(91,91,83),outline=ink,width=sw)
        d.rectangle((x-r*.40,y-r*.21,x+r*.40,y+r*.05),fill=(44,42,39),outline=ink,width=max(2,sw//2))
        d.polygon([(x,y-r*.17),(x-r*.16,y+r*.15),(x+r*.16,y+r*.15)],fill=(211,70,36))
        d.polygon([(x,y-r*.04),(x-r*.06,y+r*.15),(x+r*.07,y+r*.15)],fill=(246,184,58))
    elif name in {"snow","snowflake"}:
        for a in (0,math.pi/3,2*math.pi/3):
            dx,dy=math.cos(a)*r*.65,math.sin(a)*r*.65
            d.line((x-dx,y-dy,x+dx,y+dy),fill=ink,width=sw)
        d.ellipse((x-r*.10,y-r*.10,x+r*.10,y+r*.10),fill=paper,outline=ink,width=max(2,sw//2))
    elif name in {"mortar","pestle"}:
        d.polygon([(x-r*.54,y-r*.03),(x-r*.42,y+r*.43),(x+r*.42,y+r*.43),(x+r*.54,y-r*.03)],fill=(176,166,143),outline=ink)
        d.arc((x-r*.54,y-r*.13,x+r*.54,y+r*.13),180,360,fill=ink,width=sw)
        d.line((x+r*.05,y-r*.53,x-r*.13,y-r*.08),fill=(100,94,82),width=max(6,sw+2))
    elif name=="arrow":
        d.line((x-r*.62,y,x+r*.40,y),fill=ink,width=sw*2)
        d.polygon([(x+r*.65,y),(x+r*.30,y-r*.26),(x+r*.30,y+r*.26)],fill=ink)
    else:
        d.ellipse((x-r*.48,y-r*.48,x+r*.48,y+r*.48),fill=paper,outline=ink,width=sw)
        d.text((x,y),"?",font=e._font(int(r*.85)),fill=ink,anchor="mm")


def _draw_hook(background, hook):
    w,h=background.size;d=ImageDraw.Draw(background)
    lines=[x.strip() for x in str(hook).replace("|","\n").splitlines() if x.strip()]
    if len(lines)<2:
        words=lines[0].split() if lines else ["A Visual Explainer"]
        split=max(1,len(words)//2);lines=[" ".join(words[:split])," ".join(words[split:])]
    font_size=int(h*.030);font=e._font(font_size,bold=False)
    for line in lines[:2]:
        while d.textlength(line,font=font)>w*.93 and font_size>40:
            font_size-=2;font=e._font(font_size,bold=False)
    y1,y2=h*.293,h*.326
    d.text((w*.5,y1),lines[0],font=font,fill=(250,250,248),anchor="mm")
    line=lines[1] if len(lines)>1 else ""
    text_w=d.textlength(line,font=font);face=int(font_size*.76);gap=int(font_size*.15)
    start=(w-(text_w+gap+face))/2
    d.text((start+text_w/2,y2),line,font=font,fill=(250,250,248),anchor="mm")
    cx=start+text_w+gap+face/2;cy=y2
    d.ellipse((cx-face/2,cy-face/2,cx+face/2,cy+face/2),fill=(255,205,61),outline=(255,245,220),width=max(2,w//480))
    d.ellipse((cx-face*.18,cy-face*.13,cx-face*.07,cy-face*.02),fill=(44,40,35))
    d.ellipse((cx+face*.07,cy-face*.13,cx+face*.18,cy-face*.02),fill=(44,40,35))
    d.ellipse((cx-face*.09,cy+face*.10,cx+face*.09,cy+face*.34),fill=(44,40,35))


def _draw_background(plate, hook, size, tint):
    w,h=size
    stops=[(0.0,(0,0,0)),(.07,(3,3,3)),(.16,(14,12,10)),(.25,(34,28,23)),
           (.32,(49,40,32)),(.36,(53,43,34)),(.64,(51,41,32)),(.76,(29,23,18)),
           (.91,(5,4,4)),(1.0,(0,0,0))]
    bg=Image.new("RGB",size);gradient=ImageDraw.Draw(bg)
    for y in range(h):
        p=y/max(1,h-1)
        for j in range(len(stops)-1):
            if stops[j][0]<=p<=stops[j+1][0]:
                a,ca=stops[j];b,cb=stops[j+1];q=(p-a)/(b-a)
                color=tuple(round(ca[k]*(1-q)+cb[k]*q) for k in range(3));break
        gradient.line((0,y,w,y),fill=color)
    blur=e._cover(plate,size).filter(ImageFilter.GaussianBlur(radius=max(45,round(w*.095))))
    blur=Image.blend(blur,Image.new("RGB",size,(0,0,0)),.83)
    toned=Image.new("RGB",size,tuple(round(c*.4) for c in tint))
    blur=Image.blend(blur,toned,.22)
    mask=Image.new("L",size,0);ImageDraw.Draw(mask).rectangle((0,int(h*.12),w,int(h*.88)),fill=48)
    bg=Image.composite(blur,bg,mask.filter(ImageFilter.GaussianBlur(max(12,round(h*.035)))))
    bg=bg.convert("RGBA");_draw_hook(bg,hook)
    return bg


def _plate_for(shot, assets_dir):
    asset=shot.get("asset")
    if asset and assets_dir:
        path=(assets_dir/str(asset)).resolve()
        try:path.relative_to(assets_dir.resolve())
        except ValueError as exc:raise ValueError(f"Asset path escapes the asset directory: {asset}") from exc
        if path.is_file():
            with Image.open(path) as opened:plate=e._cover(opened.copy(),e.PLATE_SIZE)
        else:raise FileNotFoundError(f"Storyboard asset does not exist: {path}")
    else:plate=e.procedural_plate(str(shot.get("theme","generic")),str(shot.get("id","default")))
    if str(shot.get("type","illustration"))=="illustration":plate=_illustration_plate(plate,shot)
    return plate


def _render_icon_card(shot,size,t):
    w,h=size;color=e._color(shot.get("card_color"),MUSTARD)
    im=Image.new("RGBA",size,(*color,255));d=ImageDraw.Draw(im);ink=(30,29,27)
    for cx,cy,sx,sy in ((w*.035,h*.08,1,1),(w*.965,h*.08,-1,1),(w*.035,h*.93,1,-1),(w*.965,h*.93,-1,-1)):
        d.line((cx,cy,cx+sx*w*.035,cy+sy*h*.045),fill=ink,width=max(3,w//270))
        d.ellipse((cx+sx*w*.012,cy+sy*h*.005,cx+sx*w*.030,cy+sy*h*.023),fill=ink)
        d.ellipse((cx+sx*w*.025,cy+sy*h*.026,cx+sx*w*.043,cy+sy*h*.044),fill=ink)
    _icon(d,str(shot.get("icon","question")),(w*.5,h*.40),min(w*.15,h*.27))
    caption=str(shot.get("caption","")).strip()
    if caption:
        e.draw_fitted_text(d,caption,(w*.5,h*.84),int(w*.88),int(h*.105),ink,color,0,1,bold=True)
    overlay=shot.get("overlay",{})
    if isinstance(overlay,dict) and overlay.get("kind") in {"cross","tick"}:
        e._draw_tick_or_cross(d,(w*.86,h*.20),overlay["kind"]=="tick",min(w,h)*.105,color=ink,width=max(5,w//170))
    e._draw_fx(im,shot,t,str(shot.get("id","shot")))
    if shot.get("stamp"):e._draw_stamp(im,str(shot["stamp"]),t,float(shot.get("duration",1.25)),e._color(shot.get("stamp_color"),e.RED))
    return im


def _draw_comparison(card,shot,t):
    w,h=card.size;d=ImageDraw.Draw(card);ink=(34,31,28)
    left=e._color(shot.get("left_color"),(234,205,150));right=e._color(shot.get("right_color"),(166,189,142))
    d.rectangle((0,0,w//2,h),fill=left);d.rectangle((w//2,0,w,h),fill=right)
    d.line((w//2,0,w//2,h),fill=(248,239,215),width=max(5,w//160))
    _icon(d,str(shot.get("left_icon","stove")),(w*.25,h*.41),min(w*.13,h*.28))
    _icon(d,str(shot.get("right_icon","radiator")),(w*.75,h*.41),min(w*.13,h*.28))
    e.draw_fitted_text(d,str(shot.get("left_label","THEN")),(w*.25,h*.82),int(w*.43),int(h*.105),ink,left,0,1)
    e.draw_fitted_text(d,str(shot.get("right_label","NOW")),(w*.75,h*.82),int(w*.43),int(h*.105),ink,right,0,1)
    e._draw_tick_or_cross(d,(w*.25,h*.16),False,min(w,h)*.10,color=ink,width=max(5,w//180))
    e._draw_tick_or_cross(d,(w*.75,h*.16),True,min(w,h)*.10,color=ink,width=max(5,w//180))


def _render_card(shot,plate,t,duration,size):
    w,h=size;kind=str(shot.get("type","illustration"));ink=(33,30,27)
    if kind=="icon_card":return _render_icon_card(shot,size,t)
    if kind=="then_now":
        card=Image.new("RGBA",size,(0,0,0,255));_draw_comparison(card,shot,t);e._draw_fx(card,shot,t,str(shot.get("id","shot")));return card
    if kind=="statistic":
        color=e._color(shot.get("card_color"),MUSTARD);card=Image.new("RGBA",size,(*color,255));d=ImageDraw.Draw(card)
        _icon(d,"arrow",(w*.14,h*.20),min(w*.085,h*.14))
        e.draw_fitted_text(d,str(shot.get("stat",shot.get("caption","50%"))),(w*.5,h*.44),int(w*.85),int(h*.38),ink,color,0,1)
        if shot.get("subcaption"):e.draw_fitted_text(d,str(shot["subcaption"]),(w*.5,h*.78),int(w*.86),int(h*.105),ink,color,0,2)
        e._draw_fx(card,shot,t,str(shot.get("id","shot")));return card
    if kind=="method_card":
        color=e._color(shot.get("card_color"),MUSTARD);card=Image.new("RGBA",size,(*color,255));d=ImageDraw.Draw(card)
        d.polygon([(w*.30,h*.06),(w*.70,h*.06),(w*.66,h*.17),(w*.34,h*.17)],fill=(250,238,204),outline=ink)
        e.draw_fitted_text(d,f"METHOD {shot.get('number','5')}",(w*.5,h*.115),int(w*.36),int(h*.06),ink,color,0,1)
        _icon(d,str(shot.get("icon","bread")),(w*.5,h*.47),min(w*.17,h*.28))
        e.draw_fitted_text(d,str(shot.get("caption","BARK BREAD")),(w*.5,h*.83),int(w*.88),int(h*.12),ink,color,0,2)
        e._draw_fx(card,shot,t,str(shot.get("id","shot")));return card
    if kind=="face_card":
        color=e._color(shot.get("card_color"),(225,190,114));card=Image.new("RGBA",size,(*color,255));d=ImageDraw.Draw(card)
        d.polygon([(w*.30,h*.80),(w*.34,h*.57),(w*.42,h*.49),(w*.58,h*.49),(w*.66,h*.57),(w*.70,h*.80)],fill=e._color(shot.get("coat"),(105,75,51)),outline=ink)
        cx,cy=w*.5,h*.36;r=min(w*.105,h*.22)
        d.ellipse((cx-r,cy-r,cx+r,cy+r),fill=(248,238,215),outline=ink,width=max(4,w//230))
        d.arc((cx-r*.92,cy-r*1.12,cx+r*.92,cy+r*.55),180,360,fill=(84,54,38),width=max(5,w//200))
        for ex in (cx-r*.31,cx+r*.31):d.ellipse((ex-r*.07,cy-r*.05,ex+r*.01,cy+r*.05),fill=ink)
        d.arc((cx-r*.22,cy+r*.13,cx+r*.22,cy+r*.40),0,180,fill=ink,width=max(3,w//300))
        if shot.get("caption"):e.draw_fitted_text(d,str(shot["caption"]),(w*.5,h*.87),int(w*.88),int(h*.11),ink,color,0,1)
        overlay=shot.get("overlay",{})
        if isinstance(overlay,dict) and overlay.get("kind") in {"cross","tick"}:e._draw_tick_or_cross(d,(w*.84,h*.20),overlay["kind"]=="tick",min(w,h)*.10,color=ink)
        e._draw_fx(card,shot,t,str(shot.get("id","shot")));return card
    card=e._apply_camera(plate,shot,t,duration,size).convert("RGBA")
    actor=shot.get("actor")
    if isinstance(actor,dict):
        scale=float(actor.get("scale",h*.50))*w/1080.0;entrance=str(actor.get("entrance","none"))
        p=e.ease_out_cubic(t/.42) if entrance!="none" else 1.0;x_frac=float(actor.get("x",.5))
        if entrance=="left":x_frac=-.12+(x_frac+.12)*p
        elif entrance=="right":x_frac=1.12+(x_frac-1.12)*p
        elif entrance=="slide_left":x_frac=1.15+(x_frac-1.15)*p
        bob=abs(math.sin(t*2*math.pi*8.5))*scale*.025 if actor.get("pose") in {"run","running","chase"} else math.sin(t*2.2)*scale*.006
        _person(ImageDraw.Draw(card),x_frac*w,h*.95-bob,scale,e._color(actor.get("color"),(111,77,51)),str(actor.get("pose","standing")),actor.get("expression") in {"scared","worried","tired"})
    e._draw_fx(card,shot,t,str(shot.get("id","shot")))
    if shot.get("stamp"):e._draw_stamp(card,str(shot["stamp"]),t,duration,e._color(shot.get("stamp_color"),e.RED))
    overlay=shot.get("overlay",{})
    if isinstance(overlay,dict) and overlay.get("kind") in {"cross","tick"}:
        pos=overlay.get("position",[.86,.18]);e._draw_tick_or_cross(ImageDraw.Draw(card),(w*float(pos[0]),h*float(pos[1])),overlay["kind"]=="tick",min(w,h)*.10,color=ink,width=max(5,w//180))
    if shot.get("show_caption") and shot.get("caption"):
        e.draw_fitted_text(ImageDraw.Draw(card),str(shot["caption"]),(w*.5,h*.90),int(w*.88),int(h*.085),(255,255,255),(29,26,23),max(2,w//300),2)
    return card


# Patch the module globals: AnimationCompositor resolves these at frame/instance time.
e._draw_background = _draw_background
e._plate_for = _plate_for
e._render_card = _render_card
