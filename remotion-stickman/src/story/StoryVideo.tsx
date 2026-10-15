import React from "react";
import {AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame} from "remotion";
import {ChapterCard, Captions} from "../stickman/common";
import {StoryScene} from "./StoryScene";
import planData from "../generated/plan.json";
import type {StoryPlan} from "./types";

const plan = planData as StoryPlan;

export const StoryVideo: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: "#FAFAF7"}}>
      <Audio src={staticFile("story/music.wav")} volume={0.16} loop />
      <Audio src={staticFile("story/voice.wav")} volume={1} />
      {plan.chapters.map((chapter) => (
        <Sequence
          key={`chapter-card-${chapter.number}`}
          from={chapter.start_frame}
          durationInFrames={chapter.title_frames}
        >
          <ChapterCard
            n={String(chapter.number).padStart(2, "0")}
            title={chapter.title}
            sub={plan.title}
            dur={chapter.title_frames}
          />
        </Sequence>
      ))}
      {plan.chapters.flatMap((chapter) =>
        chapter.scenes.map((scene) => (
          <Sequence
            key={scene.id}
            from={scene.start_frame}
            durationInFrames={scene.duration_frames}
          >
            <StoryScene scene={scene} cast={plan.cast} title={plan.title} />
          </Sequence>
        )),
      )}
      {plan.chapters.flatMap((chapter) =>
        chapter.scenes.flatMap((scene) =>
          (scene.sfx_cues ?? []).map((cue, i) => (
            <Sequence
              key={`${scene.id}-sfx-${i}`}
              from={scene.start_frame + cue.frame}
              durationInFrames={Math.min(48, scene.duration_frames - cue.frame)}
              layout="none"
            >
              <Audio src={staticFile(`story/sfx/${cue.name}.wav`)} volume={cue.volume} />
            </Sequence>
          )),
        ),
      )}
      {plan.captions.length > 0 && (
        <Captions
          cues={plan.captions}
          bottom={plan.height > plan.width ? 100 : 48}
          size={plan.height > plan.width ? 42 : 36}
        />
      )}
    </AbsoluteFill>
  );
};
