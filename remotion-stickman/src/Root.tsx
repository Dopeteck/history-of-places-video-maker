import React from "react";
import {Composition} from "remotion";
import plan from "./generated/plan.json";
import {StoryVideo} from "./story/StoryVideo";
import type {StoryPlan} from "./story/types";

const storyPlan = plan as StoryPlan;

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ChoreographedStory"
    component={StoryVideo}
    durationInFrames={storyPlan.total_frames}
    fps={storyPlan.fps}
    width={storyPlan.width}
    height={storyPlan.height}
    defaultProps={{}}
  />
);
