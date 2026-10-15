export type Emotion = "neutral" | "happy" | "angry" | "shocked" | "sad" | "sly" | "dead";
export type Action =
  | "idle" | "talk" | "walk" | "run" | "point" | "cheer" | "shrug" | "think"
  | "crouch" | "punch" | "kick" | "carry" | "hold" | "facepalm" | "sit"
  | "handshake" | "sad" | "shock" | "fall" | "kneel" | "laugh" | "dead";

export type CastMember = {
  id: string;
  name: string;
  accessory: string;
  accent: string;
};

export type ActorState = {
  id: string;
  action: Action;
  x: number;
  emotion: Emotion;
};

export type ActionBeat = {
  at: number;
  actions: ActorState[];
};

export type StoryScene = {
  id: string;
  title: string;
  start_frame: number;
  duration_frames: number;
  narration: string;
  background: string;
  camera: string;
  effects: string[];
  props: string[];
  emphasis_words: string[];
  characters_on_screen: {id: string; x: number; pose: string; expression: string}[];
  action_beats: ActionBeat[];
  sfx_cues?: {frame: number; name: string; volume: number}[];
};

export type CaptionCue = {startFrame: number; endFrame: number; text: string};

export type StoryChapter = {
  number: number;
  title: string;
  start_frame: number;
  title_frames: number;
  scenes: StoryScene[];
};

export type StoryPlan = {
  schema_version: number;
  title: string;
  hook: string;
  mode: "long" | "shorts";
  width: number;
  height: number;
  fps: number;
  total_frames: number;
  cast: CastMember[];
  chapters: StoryChapter[];
  captions: CaptionCue[];
};
