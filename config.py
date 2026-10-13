"""Central configuration for the stickman video generator.

V2 supports both short-form portrait videos and longer landscape/portrait videos,
while keeping every visual asset local to the repository.
"""
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
ASSETS_DIR = ROOT / "assets"
CHARACTERS_DIR = ASSETS_DIR / "characters"
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
OUTPUT_DIR = ROOT / "output"
WORK_DIR = ROOT / "work"

# Runtime defaults. main.py calls apply_runtime_profile() before rendering.
VIDEO_W, VIDEO_H, FPS = 1080, 1920, 30
LINE_GAP = 0.20
TAIL_PAD = 0.6
MODE = "short"
ASPECT = "portrait"
TARGET_MINUTES = 0.7
TARGET_WORDS_MIN = 85
TARGET_WORDS_MAX = 120
MIN_SCENES = 6
MAX_SCENES = 10

VOICES = {
    "A": {"voice": "en-US-GuyNeural", "rate": "+8%", "pitch": "+0Hz"},
    "B": {"voice": "en-US-AnaNeural", "rate": "+8%", "pitch": "+0Hz"},
}

# Layout values are recalculated by apply_runtime_profile().
CHAR_MAX_W = 480
CHAR_MAX_H = 760
CHAR_X = {"A": 290, "B": 790}
CHAR_BOTTOM_Y = 1330
BOUNCE_PX = 16

ALLOWED_POSES = ["standing", "talking", "shocked", "laughing", "thinking"]

FONT_NAME = "DejaVu Sans"
CAPTION_FONT_SIZE = 84
CAPTION_MARGIN_V = 380
CAPTION_WORDS_PER_CHUNK = 3
SHOW_TITLE = True
TITLE_FONT_SIZE = 64
TITLE_MARGIN_V = 230

MAX_RETRIES_PER_MODEL = 2
PROVIDERS = [
    {
        "name": "groq",
        "type": "openai",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "json_mode": True,
    },
    {
        "name": "gemini",
        "type": "gemini",
        "env_key": "GEMINI_API_KEY",
        "models": ["gemini-2.5-flash"],
    },
    {
        "name": "openrouter",
        "type": "openai",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["deepseek/deepseek-r1:free"],
        "json_mode": False,
    },
]

SYSTEM_PROMPT = """You are a highly engaging stickman-video scriptwriter.
Two characters talk: Speaker A is "$char_a" and Speaker B is "$char_b".
Return ONE valid JSON object and nothing else (no markdown, no commentary).

Rules:
- $min_scenes to $max_scenes scenes, approximately $min_words to $max_words spoken words in total.
- Each scene is one spoken line, maximum 22 words, plain text only (no emojis, no stage directions).
- Keep the conversation moving; avoid filler and repeated explanations.
- The first line must be a strong hook. The ending must deliver a payoff, twist, useful conclusion, or call to follow.
- "pose" is the speaker's pose, "reaction" is the listener's pose.
- Both poses must be one of: $poses.

JSON schema:
{
  "title": "hook title, max 80 chars",
  "description": "1-2 sentence post description",
  "hashtags": ["#stickman", "#video"],
  "scenes": [
    {"speaker": "A", "text": "line", "pose": "talking", "reaction": "standing"}
  ]
}
"""

FORMATS = {
    "history": {
        "cast": {"A": "teacher", "B": "student"},
        "background": "classroom",
        "prompt": (
            "Write an entertaining, accurate HISTORY dialogue about: $topic. "
            "The teacher explains and the student challenges, reacts, and asks useful questions. Facts must be true."
        ),
        "seeds": [
            "Cleopatra lived closer in time to the moon landing than to the pyramids being built",
            "Oxford University is older than the Aztec Empire",
            "the shortest war in history lasted under an hour",
            "Vikings never actually wore horned helmets",
            "Napoleon was not unusually short",
            "medieval people bathed more than most people think",
        ],
    },
    "explainer": {
        "cast": {"A": "teacher", "B": "student"},
        "background": "studio",
        "prompt": (
            "Write a clear, lively EXPLAINER dialogue about: $topic. "
            "Start with the surprising part, explain it simply, use concrete examples, and end with a memorable takeaway."
        ),
        "seeds": [
            "why procrastination feels good in the moment",
            "how compound interest actually works",
            "why airplanes can stay in the sky",
            "how recommendation algorithms learn what you like",
            "why sleep changes memory",
        ],
    },
    "story": {
        "cast": {"A": "narrator", "B": "friend"},
        "background": "room",
        "prompt": (
            "Write a compelling STORY dialogue about: $topic. "
            "Build curiosity, escalate the situation scene by scene, and finish with a satisfying reveal or lesson."
        ),
        "seeds": [
            "a student who found a strange note inside a library book",
            "a man who kept receiving calls from his own number",
            "the cheapest decision that accidentally saved a business",
            "a tiny mistake that changed an entire day",
        ],
    },
    "joke": {
        "cast": {"A": "peasant", "B": "knight"},
        "background": "tavern",
        "prompt": (
            "Write a clean, punchy medieval COMEDY dialogue about: $topic. "
            "Use escalating setups and callbacks, with a clear payoff at the end."
        ),
        "seeds": [
            "a knight who is afraid of his own horse",
            "a peasant who invented the first complaint box",
            "a dragon who files a noise complaint",
            "a peasant giving terrible career advice",
        ],
    },
    "riddle": {
        "cast": {"A": "stranger", "B": "peasant"},
        "background": "street",
        "prompt": (
            "Write a RIDDLE dialogue about: $topic. The stranger presents the puzzle, the peasant reasons through it, "
            "makes at least one believable wrong guess, then the answer is revealed clearly."
        ),
        "seeds": [
            "something that gets wetter the more it dries",
            "something with keys but no locks",
            "something that gets bigger the more you take away",
        ],
    },
}

# These names correspond to local PNG files. Missing files are generated locally
# as simple gradients, so scheduled runs never depend on stock-media services.
BACKGROUND_PALETTES = {
    "classroom": ((210, 225, 205), (95, 130, 100)),
    "studio": ((225, 225, 235), (90, 95, 125)),
    "room": ((235, 220, 200), (125, 95, 75)),
    "tavern": ((190, 130, 85), (65, 35, 20)),
    "street": ((185, 210, 235), (95, 105, 130)),
}


def apply_runtime_profile(mode: str = "short", aspect: str = "auto", minutes: float | None = None) -> None:
    """Mutate layout/script targets for the requested render profile."""
    global VIDEO_W, VIDEO_H, MODE, ASPECT, TARGET_MINUTES
    global TARGET_WORDS_MIN, TARGET_WORDS_MAX, MIN_SCENES, MAX_SCENES
    global CHAR_MAX_W, CHAR_MAX_H, CHAR_X, CHAR_BOTTOM_Y, BOUNCE_PX
    global CAPTION_FONT_SIZE, CAPTION_MARGIN_V, TITLE_FONT_SIZE, TITLE_MARGIN_V, CAPTION_WORDS_PER_CHUNK

    MODE = mode
    if aspect == "auto":
        aspect = "portrait" if mode == "short" else "landscape"
    ASPECT = aspect

    if minutes is None:
        minutes = 0.7 if mode == "short" else 3.0
    TARGET_MINUTES = max(0.35, min(float(minutes), 8.0))

    # Natural dialogue lands around 130-150 wpm. Give the model a reasonable band.
    target_words = int(TARGET_MINUTES * 140)
    TARGET_WORDS_MIN = max(55, int(target_words * 0.88))
    TARGET_WORDS_MAX = max(TARGET_WORDS_MIN + 20, int(target_words * 1.12))
    MIN_SCENES = max(5, int(TARGET_WORDS_MIN / 18))
    MAX_SCENES = min(90, max(MIN_SCENES + 2, int(TARGET_WORDS_MAX / 11)))

    if aspect == "landscape":
        VIDEO_W, VIDEO_H = 1920, 1080
        CHAR_MAX_W, CHAR_MAX_H = 560, 700
        CHAR_X = {"A": 560, "B": 1360}
        CHAR_BOTTOM_Y = 880
        BOUNCE_PX = 12
        CAPTION_FONT_SIZE = 58
        CAPTION_MARGIN_V = 90
        TITLE_FONT_SIZE = 54
        TITLE_MARGIN_V = 55
        CAPTION_WORDS_PER_CHUNK = 6
    else:
        VIDEO_W, VIDEO_H = 1080, 1920
        CHAR_MAX_W, CHAR_MAX_H = 480, 760
        CHAR_X = {"A": 290, "B": 790}
        CHAR_BOTTOM_Y = 1330
        BOUNCE_PX = 16
        CAPTION_FONT_SIZE = 84
        CAPTION_MARGIN_V = 380
        TITLE_FONT_SIZE = 64
        TITLE_MARGIN_V = 230
        CAPTION_WORDS_PER_CHUNK = 3
