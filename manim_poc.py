"""Manim proof of concept: vector motion over detailed, cached scene artwork.

This is deliberately isolated from the production renderer. It reads no script or network
input: the sequence below is a hand-authored, repeatable visual benchmark.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from manim import (
    Arc,
    Circle,
    Dot,
    ImageMobject,
    Line,
    MovingCameraScene,
    ORIGIN,
    Polygon,
    Rectangle,
    Text,
    ValueTracker,
    VGroup,
    UP,
    DOWN,
    LEFT,
    RIGHT,
    PI,
    ParametricFunction,
    WHITE,
    BLACK,
    always_redraw,
    config,
    linear,
    smooth,
    GrowFromCenter,
    Create,
    FadeIn,
    FadeOut,
    AnimationGroup,
    LaggedStart,
)

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Existing, deterministic, offline illustration helpers from this branch.
import reference_art_direction as art  # noqa: E402

# Logical frame: 8 units wide x 14.222 units high, matching a 9:16 output.
FRAME_W = 8.0
FRAME_H = FRAME_W * 16.0 / 9.0
PIXELS = (1080, 1920)
INK = "#282622"
PAPER = "#F6EED8"
MUSTARD = "#E8B94F"
COAT = "#8C5639"
TINTS = {
    "winter_room": (101, 70, 49),
    "village_street": (113, 83, 59),
    "market": (135, 87, 55),
    "kitchen": (130, 91, 61),
    "snow_forest": (108, 127, 132),
}

# Apply the logical portrait frame before Manim constructs the scene camera.
# CLI pixel resolution remains explicit in tools/render_manim_poc.py.
config.frame_width = FRAME_W
config.frame_rate = 30


def _plate(theme: str, key: str) -> Image.Image:
    shot = {"id": key, "type": "illustration", "theme": theme}
    return art._plate_for(shot, None)


def _backdrop(plate: Image.Image, tint: tuple[int, int, int]) -> Image.Image:
    """Dim, warm blurred full-height backdrop, without rasterized headline text."""
    im = ImageOps.fit(
        plate.convert("RGB"), PIXELS, method=Image.Resampling.LANCZOS
    ).filter(ImageFilter.GaussianBlur(radius=94))
    im = Image.blend(im, Image.new("RGB", PIXELS, (0, 0, 0)), 0.80)
    wash = Image.new("RGBA", PIXELS, (*tint, 26))
    im = Image.alpha_composite(im.convert("RGBA"), wash)
    shade = Image.new("RGBA", PIXELS, (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    for y in range(PIXELS[1]):
        p = y / (PIXELS[1] - 1)
        alpha = int(182 + 60 * abs(2 * p - 1))
        d.line((0, y, PIXELS[0], y), fill=(0, 0, 0, min(235, alpha)))
    return Image.alpha_composite(im, shade).convert("RGB")


def _image_mobject(im: Image.Image, *, full_frame: bool = False):
    mob = ImageMobject(np.asarray(im.convert("RGB")))
    if full_frame:
        mob.stretch_to_fit_width(FRAME_W)
        mob.stretch_to_fit_height(FRAME_H)
        mob.move_to(ORIGIN)
        mob.set_z_index(-10, family=True)
    else:
        mob.stretch_to_fit_width(FRAME_W)
        mob.move_to(ORIGIN)
        mob.set_z_index(0, family=True)
    return mob


def _line(start, end, width: float = 3.4):
    return always_redraw(
        lambda: Line(start(), end(), color=INK, stroke_width=width)
    )


def articulated_stickman(x_position: ValueTracker, gait: ValueTracker) -> VGroup:
    """Vector stickman with a cloth tunic, independent limbs and a smooth gait."""
    ground = -1.73

    def x():
        return x_position.get_value()

    def swing():
        return math.sin(gait.get_value())

    def head_center():
        return np.array([x(), ground + 1.91, 0.0])

    def shoulder(side: float):
        return np.array([x() + side * 0.16, ground + 1.53, 0.0])

    def hip(side: float):
        return np.array([x() + side * 0.055, ground + 0.76, 0.0])

    # Cloth silhouette provides a little visual mass and a distinct period costume.
    tunic = always_redraw(
        lambda: Polygon(
            np.array([x() - 0.18, ground + 1.56, 0]),
            np.array([x() + 0.18, ground + 1.56, 0]),
            np.array([x() + 0.31, ground + 0.80, 0]),
            np.array([x() - 0.27, ground + 0.80, 0]),
            color=INK,
            stroke_width=3.1,
            fill_color=COAT,
            fill_opacity=1,
        )
    )
    belt = _line(
        lambda: np.array([x() - 0.25, ground + 0.98, 0]),
        lambda: np.array([x() + 0.27, ground + 0.98, 0]),
        3.2,
    )
    neck = _line(
        lambda: np.array([x(), ground + 1.70, 0]),
        lambda: np.array([x(), ground + 1.51, 0]),
        3.4,
    )
    head = always_redraw(
        lambda: Circle(
            radius=0.19,
            color=INK,
            stroke_width=3,
            fill_color=PAPER,
            fill_opacity=1,
        ).move_to(head_center())
    )
    hood = always_redraw(
        lambda: Arc(
            radius=0.205,
            start_angle=PI * 0.08,
            angle=PI * 0.84,
            color=COAT,
            stroke_width=8,
        ).move_to(head_center() + UP * 0.025)
    )
    eyes = always_redraw(
        lambda: VGroup(
            Dot(head_center() + LEFT * 0.055 + UP * 0.025, radius=0.022, color=INK),
            Dot(head_center() + RIGHT * 0.055 + UP * 0.025, radius=0.022, color=INK),
            Arc(
                radius=0.046,
                start_angle=PI * 1.08,
                angle=PI * 0.84,
                color=INK,
                stroke_width=2.0,
            ).move_to(head_center() + DOWN * 0.052),
        )
    )
    torso = _line(
        lambda: np.array([x(), ground + 1.52, 0]),
        lambda: np.array([x(), ground + 0.78, 0]),
        3.2,
    )

    # Alternating arms: the elbow/wrist motion derives continuously from the gait tracker.
    left_arm_upper = _line(
        lambda: shoulder(-1),
        lambda: np.array([x() - 0.35 - 0.10 * swing(), ground + 1.20, 0]),
        4.0,
    )
    left_arm_lower = _line(
        lambda: np.array([x() - 0.35 - 0.10 * swing(), ground + 1.20, 0]),
        lambda: np.array([x() - 0.30 - 0.22 * swing(), ground + 1.02, 0]),
        3.5,
    )
    right_arm_upper = _line(
        lambda: shoulder(1),
        lambda: np.array([x() + 0.35 + 0.10 * swing(), ground + 1.20, 0]),
        4.0,
    )
    right_arm_lower = _line(
        lambda: np.array([x() + 0.35 + 0.10 * swing(), ground + 1.20, 0]),
        lambda: np.array([x() + 0.30 + 0.22 * swing(), ground + 1.02, 0]),
        3.5,
    )

    left_leg_upper = _line(
        lambda: hip(-1),
        lambda: np.array([x() - 0.18 + 0.20 * swing(), ground + 0.40, 0]),
        4.2,
    )
    left_leg_lower = _line(
        lambda: np.array([x() - 0.18 + 0.20 * swing(), ground + 0.40, 0]),
        lambda: np.array([x() - 0.25 + 0.37 * swing(), ground + 0.08, 0]),
        3.8,
    )
    right_leg_upper = _line(
        lambda: hip(1),
        lambda: np.array([x() + 0.18 - 0.20 * swing(), ground + 0.40, 0]),
        4.2,
    )
    right_leg_lower = _line(
        lambda: np.array([x() + 0.18 - 0.20 * swing(), ground + 0.40, 0]),
        lambda: np.array([x() + 0.25 - 0.37 * swing(), ground + 0.08, 0]),
        3.8,
    )

    boots = always_redraw(
        lambda: VGroup(
            Line(
                [x() - 0.32 + 0.37 * swing(), ground + 0.08, 0],
                [x() - 0.16 + 0.37 * swing(), ground + 0.08, 0],
                color=INK,
                stroke_width=5,
            ),
            Line(
                [x() + 0.18 - 0.37 * swing(), ground + 0.08, 0],
                [x() + 0.34 - 0.37 * swing(), ground + 0.08, 0],
                color=INK,
                stroke_width=5,
            ),
        )
    )
    parts = VGroup(
        tunic,
        belt,
        neck,
        torso,
        left_arm_upper,
        left_arm_lower,
        right_arm_upper,
        right_arm_lower,
        left_leg_upper,
        left_leg_lower,
        right_leg_upper,
        right_leg_lower,
        boots,
        head,
        hood,
        eyes,
    )
    parts.set_z_index(4, family=True)
    return parts


def _hook_group() -> VGroup:
    # Text is rendered as vector outlines by Manim/Pango, never baked into the background.
    first = Text(
        "CRAZIEST Survival Methods",
        font="DejaVu Sans",
        font_size=26,
        color=WHITE,
        weight="NORMAL",
    )
    first.scale_to_fit_width(6.05).move_to(np.array([0, 3.27, 0]))
    second = Text(
        "Used By Medieval People",
        font="DejaVu Sans",
        font_size=26,
        color=WHITE,
        weight="NORMAL",
    )
    second.scale_to_fit_width(5.10).move_to(np.array([-0.10, 2.85, 0]))
    face_center = np.array([2.68, 2.85, 0])
    face = Circle(
        radius=0.16,
        color="#FFE9A0",
        stroke_width=2.4,
        fill_color="#F8C844",
        fill_opacity=1,
    ).move_to(face_center)
    eyes = VGroup(
        Dot(face_center + LEFT * 0.052 + UP * 0.035, radius=0.018, color=INK),
        Dot(face_center + RIGHT * 0.052 + UP * 0.035, radius=0.018, color=INK),
        Arc(
            radius=0.054,
            start_angle=PI * 1.08,
            angle=PI * 0.84,
            color=INK,
            stroke_width=2.2,
        ).move_to(face_center + DOWN * 0.028),
    )
    result = VGroup(first, second, face, eyes)
    result.set_z_index(10, family=True)
    return result


class ManimHybridProof(MovingCameraScene):
    """A short portrait benchmark with hard cuts, an articulated walk and infographic cards."""

    def _cut(self, theme: str, key: str, tint: tuple[int, int, int] | None = None,
             illustrated_card: bool = True) -> Image.Image:
        # Remove the previous scene and all foreground objects immediately: hard cut only.
        removable = []
        if hasattr(self, "backdrop_mob"):
            removable.append(self.backdrop_mob)
        if hasattr(self, "card_mob") and self.card_mob is not None:
            removable.append(self.card_mob)
        if hasattr(self, "transients"):
            removable.extend(self.transients)
        if removable:
            self.remove(*removable)
        self.transients = []

        plate = _plate(theme, key)
        bg = _backdrop(plate, tint or TINTS.get(theme, (115, 82, 59)))
        self.backdrop_mob = _image_mobject(bg, full_frame=True)
        self.backdrop_mob.set_z_index(-10, family=True)
        self.card_mob = _image_mobject(plate) if illustrated_card else None
        self.add(self.backdrop_mob)
        if self.card_mob is not None:
            self.add(self.card_mob)
        # Keep the persistent hook in front after each background/card replacement.
        self.add(self.hook)
        return plate

    def _track(self, *mobjects):
        for mob in mobjects:
            mob.set_z_index(5, family=True)
            self.add(mob)
            self.transients.append(mob)

    def construct(self):
        self.transients = []
        self.hook = _hook_group()
        self.camera.background_color = "#080706"

        # 1) Storybook hearth: richly drawn art, subtle push-in.
        plate = self._cut("winter_room", "manim_sleeping_bed")
        self.add(self.hook)
        self.play(
            self.card_mob.animate.scale(1.055).shift(LEFT * 0.10),
            run_time=1.25,
            rate_func=smooth,
        )

        # 2) Hard cut to the street. The coat, head, hands and separate limbs animate smoothly.
        self._cut("village_street", "manim_walk_street")
        x_tracker = ValueTracker(-2.05)
        gait = ValueTracker(0.0)
        figure = articulated_stickman(x_tracker, gait)
        self._track(figure)
        self.play(
            x_tracker.animate.set_value(1.95),
            gait.animate.set_value(4 * PI),
            run_time=2.20,
            rate_func=linear,
        )

        # 3) Market scene; camera-like push/pan is applied to the illustration only,
        # so the headline and dark portrait background remain locked like the reference.
        self._cut("market", "manim_market_pan")
        self.play(
            self.card_mob.animate.scale(1.075).shift(LEFT * 0.13 + UP * 0.035),
            run_time=1.55,
            rate_func=smooth,
        )

        # 4) Reference-style yellow statistic card; type pops in on the cut.
        self._cut("village_street", "manim_statistic_backdrop",
                  tint=(183, 137, 52), illustrated_card=False)
        yellow = Rectangle(
            width=FRAME_W,
            height=4.5,
            stroke_width=0,
            fill_color=MUSTARD,
            fill_opacity=1,
        ).move_to(ORIGIN)
        arrow = VGroup(
            Line([-3.05, 1.12, 0], [-2.43, 1.12, 0], color=INK, stroke_width=4.0),
            Line([-2.43, 1.12, 0], [-2.63, 1.25, 0], color=INK, stroke_width=4.0),
            Line([-2.43, 1.12, 0], [-2.63, 0.99, 0], color=INK, stroke_width=4.0),
        )
        percentage = Text("50%", font="DejaVu Sans", font_size=94,
                          color=INK, weight="BOLD").move_to(UP * 0.28)
        subtitle = Text("OF THE VILLAGE", font="DejaVu Sans", font_size=27,
                        color=INK, weight="BOLD").move_to(DOWN * 1.28)
        stat = VGroup(yellow, arrow, percentage, subtitle)
        stat.set_z_index(1, family=True)
        self._track(stat)
        self.play(GrowFromCenter(percentage), run_time=0.26, rate_func=smooth)
        self.play(FadeIn(subtitle, shift=UP * 0.08), run_time=0.22)
        self.wait(0.66)

        # 5) Kitchen art and curling steam. This camera move scales only the art card.
        plate = self._cut("kitchen", "manim_pottage_cooking")
        steam_lines = VGroup()
        for index, x_pos in enumerate((-0.10, 0.03, 0.16)):
            phase = index * 0.55
            steam_lines.add(
                ParametricFunction(
                    lambda t, x_pos=x_pos, phase=phase: np.array([
                        0.15 + x_pos + 0.055 * math.sin(2 * PI * t + phase),
                        -0.05 + 0.68 * t,
                        0,
                    ]),
                    t_range=[0, 1],
                    color="#F7EED8",
                    stroke_width=4.5,
                )
            )
        self._track(steam_lines)
        self.play(
            self.card_mob.animate.scale(1.06).shift(RIGHT * 0.08),
            steam_lines.animate.shift(UP * 0.14),
            run_time=1.18,
            rate_func=smooth,
        )

        # 6) Bold pottage pictogram card: large black cauldron, cream steam and label.
        self._cut("kitchen", "manim_pottage_infographic", tint=(178, 139, 66),
                  illustrated_card=False)
        yellow = Rectangle(
            width=FRAME_W,
            height=4.5,
            stroke_width=0,
            fill_color=MUSTARD,
            fill_opacity=1,
        ).move_to(ORIGIN)
        cauldron_body = Polygon(
            [-0.92, 0.25, 0], [0.92, 0.25, 0], [0.68, -0.70, 0],
            [-0.68, -0.70, 0],
            color=INK,
            stroke_width=4,
            fill_color="#282623",
            fill_opacity=1,
        )
        pot_rim = Circle(
            radius=0.70,
            color=INK,
            stroke_width=4,
            fill_color="#3B3832",
            fill_opacity=1,
        ).stretch_to_fit_width(1.82).stretch_to_fit_height(0.38).move_to(UP * 0.28)
        feet = VGroup(
            Line([-0.55, -0.66, 0], [-0.68, -0.92, 0], color=INK, stroke_width=5),
            Line([0.55, -0.66, 0], [0.68, -0.92, 0], color=INK, stroke_width=5),
        )
        steam = VGroup()
        for index, xx in enumerate((-0.32, 0.0, 0.32)):
            phase = index * 0.65
            steam.add(
                ParametricFunction(
                    lambda t, xx=xx, phase=phase: np.array([
                        xx + 0.08 * math.sin(2 * PI * t + phase),
                        0.47 + 0.56 * t,
                        0,
                    ]),
                    t_range=[0, 1],
                    color=PAPER,
                    stroke_width=5.0,
                )
            )
        label = Text("POTTAGE.", font="DejaVu Sans", font_size=32,
                     color=INK, weight="BOLD").move_to(DOWN * 1.48)
        pot_card = VGroup(yellow, cauldron_body, pot_rim, feet, steam, label)
        pot_card.set_z_index(1, family=True)
        self._track(pot_card)
        self.play(LaggedStart(*[Create(item) for item in steam], lag_ratio=0.12,
                              run_time=0.35))
        self.play(
            pot_card.animate.scale(1.025),
            run_time=0.85,
            rate_func=smooth,
        )

        # 7) Snowy forest outro: fast hard cut and a gentle pull-back.
        self._cut("snow_forest", "manim_snow_forest_outro")
        snow = VGroup()
        for index in range(22):
            px = -3.85 + (index * 0.73) % 7.7
            py = 1.85 - (index * 0.49) % 3.9
            dot = Dot(
                np.array([px, py, 0]),
                radius=0.012 + (index % 3) * 0.004,
                color="#E8F0EF",
            )
            dot.set_opacity(0.65)
            snow.add(dot)
        self._track(snow)
        self.play(
            AnimationGroup(
                self.card_mob.animate.scale(0.955).shift(RIGHT * 0.06),
                *[dot.animate.shift(DOWN * (0.35 + (i % 3) * 0.10) + RIGHT * 0.10)
                  for i, dot in enumerate(snow)],
                lag_ratio=0,
                run_time=1.20,
            ),
            rate_func=smooth,
        )

