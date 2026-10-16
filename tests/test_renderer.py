import tempfile
import unittest
from pathlib import Path
from PIL import Image
import numpy as np
from captions import build_ass,stamp,word_timings
from characters import character, resolve_character_asset
from story import POSES,ACCESSORIES,EXPRESSIONS,BACKGROUNDS,PROPS
from render import timeline,chapter_audio,smoke_story
from scenes import backdrop,Scene,prop_icon
from tts import duration,write_wav,Narrator

class RendererTests(unittest.TestCase):
    def test_ass_colors_and_injection(self):
        ass=build_ass([{'start':2.5,'words':word_timings(r'hello {\pos(0,0)} world',2)}],(1080,1920))
        for c in ['&H00A6A21F','&H002B2B2B','&H00E3F1F7']:self.assertIn(c,ass)
        self.assertIn(r'\k',ass);self.assertNotIn(r'\pos',ass);self.assertIn('0:00:02.50',ass);self.assertEqual(stamp(3599.999),'1:00:00.00')
    def test_timeline_no_overlap(self):
        entries,total=timeline([1.001,2.005,3.0])
        for a,b in zip(entries,entries[1:]):self.assertAlmostEqual(a['start']+a['duration'],b['start'])
        self.assertAlmostEqual(entries[-1]['start']+entries[-1]['duration'],total)
        with self.assertRaises(ValueError):timeline([float('nan')])
    def test_audio_measured_and_padded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.wav';write_wav(p,np.ones(24024)*.01);self.assertAlmostEqual(duration(p),1.001)
            entries,total=timeline([duration(p)]);out=Path(d)/'full.wav';chapter_audio(out,[{'path':str(p)}],entries,total);self.assertAlmostEqual(duration(out),total,places=4)
            with self.assertRaises(ValueError):write_wav(p,[])
            with self.assertRaises(ValueError):write_wav(p,[np.nan])
    def test_all_visual_enums(self):
        for p in POSES:
            im=character('x',p,'neutral','none','#1FA2A6',180);self.assertEqual(im.size,(180,180));self.assertIsNotNone(im.getbbox())
        for a in ACCESSORIES:
            for e in EXPRESSIONS:character('x','standing',e,a,'#E0A526',160)
        for b in BACKGROUNDS:self.assertEqual(backdrop(b,(320,180)).size,(320,180))
        im=Image.new('RGB',(320,180))
        for p in PROPS:prop_icon(im,p,100,100,20)
    def test_png_override_and_missing_assets(self):
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGBA',(40,80),'red').save(Path(d)/'custom_standing.png')
            im=character('custom','standing','neutral','none','#1FA2A6',100,assets=d);self.assertEqual(im.getpixel((50,99)),(255,0,0,255))
            character('absent','standing','neutral','crown','#E0A526',100,assets=d);self.assertEqual(len(list(Path(d).iterdir())),1)
    def test_pose_packs_keep_character_identity_consistent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for folder in ('stickman_standing_idle_pose','stickman_character_pointing_or_e',
                           'stickman_character_in_surprised','stickman_running_against_magenta',
                           'stickman_crouching_and_searching','stickman_celebrating_new_pose',
                           'character_asset_with_yellow_turban','elder_character_in_neutral_pose'):
                target=root/folder;target.mkdir()
                Image.new('RGB',(40,60),(255,0,255)).save(target/'asset.jpg')
            expected={
                'standing':'stickman_standing_idle_pose',
                'talking':'stickman_standing_idle_pose',
                'pointing':'stickman_character_pointing_or_e',
                'shocked':'stickman_character_in_surprised',
                'running':'stickman_running_against_magenta',
                'kneeling':'stickman_crouching_and_searching',
                'laughing':'stickman_celebrating_new_pose',
                'fighting':'stickman_standing_idle_pose',
                'falling':'stickman_standing_idle_pose',
                'dead':'stickman_standing_idle_pose',
                'thinking':'stickman_standing_idle_pose',
                'praying':'stickman_standing_idle_pose',
            }
            for pose,folder in expected.items():
                self.assertEqual(resolve_character_asset(root,'speaker_a',pose,'none').parent.name,folder)
            royal={resolve_character_asset(root,'mansa_musa',pose,'crown') for pose in expected}
            self.assertEqual(len(royal),1)
            elder={resolve_character_asset(root,'elder_advisor',pose,'beard') for pose in expected}
            self.assertEqual(len(elder),1)

    def test_nested_jpg_magenta_key_is_transparent(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'stickman_standing_idle_pose';folder.mkdir()
            src=Image.new('RGB',(80,120),(255,0,255))
            for x in range(25,55):
                for y in range(20,100):src.putpixel((x,y),(30,180,80))
            src.save(folder/'character_standing_in_idle_pose.jpg',quality=100)
            im=character('cast_a','standing','neutral','none','#1FA2A6',120,assets=d)
            self.assertEqual(im.mode,'RGBA')
            self.assertEqual(im.getpixel((60,60))[3],255)
            self.assertEqual(im.getpixel((22,10))[3],0)  # magenta source pixel, not empty canvas
            self.assertTrue(any(p.suffix=='.jpg' for p in folder.iterdir()))
    def test_scene_determinism_and_motion(self):
        story=smoke_story();data=story['chapters'][0]['scenes'][2];cast={c['id']:c for c in story['outline']['cast']};scene=Scene(data,cast,(320,568),5,word_timings(data['narration'],5))
        self.assertEqual(scene.frame(1).tobytes(),scene.frame(1).tobytes());self.assertNotEqual(scene.frame(1).tobytes(),scene.frame(2).tobytes())
    def test_voice_constraints(self):
        with self.assertRaises(ValueError):Narrator('unknown')
        with self.assertRaises(ValueError):Narrator(speed=0)
        with self.assertRaises(ValueError):word_timings('',1)

if __name__=='__main__':unittest.main()
