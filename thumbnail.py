"""Original thumbnail using the procedural cast."""
from PIL import Image,ImageDraw
from characters import character,IVORY,TEAL
from scenes import centered_text

def create_thumbnail(title,cast,path):
    im=Image.new('RGBA',(1280,720),IVORY);c=cast[0]
    im.alpha_composite(character(c['id'],'shocked','shocked',c['accessory'],c['accent'],590),(685,95))
    panel=Image.new('RGB',(700,720),IVORY);centered_text(panel,' '.join(title.split()[:7]),170,620,70);im.paste(panel,(0,0))
    d=ImageDraw.Draw(im);d.line((590,500,810,390),fill=TEAL,width=22);d.polygon([(810,390),(738,398),(785,456)],fill=TEAL);im.convert('RGB').save(path)
