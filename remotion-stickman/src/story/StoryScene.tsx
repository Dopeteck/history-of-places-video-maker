import React from "react";
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from "remotion";
import {Actor, P, addPose, groundY, idle, runCycle, walkCycle} from "../stickman/stick";
import type {Joints, Key, Pose} from "../stickman/stick";
import {Stage} from "../stickman/common";
import {Slam} from "../stickman/fx";
import {At, Barrel, Bill, Box, Chain, Coin, Flag, GoldBar, Shield} from "../stickman/props";
import type {Action, ActionBeat, ActorState, CastMember, Emotion, StoryScene as StorySceneData} from "./types";

const NEUTRAL: Partial<Pose> = {
  lean: 0, head: 0, la1: -18, la2: 8, ra1: 18, ra2: -8,
  ll1: -12, ll2: 4, rl1: 12, rl2: -4, rot: 0,
};

const ACTION_POSES: Record<Action, Partial<Pose>> = {
  idle: {},
  talk: {la1: -48, la2: -30, ra1: 50, ra2: 34, head: -3},
  walk: {},
  run: {lean: 12},
  point: P.pointR,
  cheer: P.cheer,
  shrug: P.shrug,
  think: P.think,
  crouch: P.crouch,
  punch: P.punchR,
  kick: P.kickR,
  carry: P.carry,
  hold: P.hold,
  facepalm: P.facepalm,
  sit: P.sit,
  handshake: P.handshake,
  sad: P.sad,
  shock: P.shock,
  fall: {lean: 18, head: 18, rot: 76, la1: -100, la2: -45, ra1: 105, ra2: 30, ll1: -45, ll2: 30, rl1: 65, rl2: 55},
  kneel: P.crouch,
  laugh: P.cheer,
  dead: {lean: 0, head: 22, rot: 88, la1: -120, la2: -10, ra1: 110, ra2: 15, ll1: 85, ll2: -65, rl1: -75, rl2: 60},
};

const poseFor = (action: Action, x: number, y: number, scale: number): Partial<Pose> => {
  const custom = ACTION_POSES[action] ?? {};
  // Upstream ready-poses use the 1920x1080 coordinate system. Keep their vertical offsets
  // relative to the normal standing hip so they also work in vertical compositions.
  const poseY = typeof custom.y === "number" ? custom.y : 760;
  return {
    ...NEUTRAL,
    ...custom,
    x,
    y: y + (poseY - 760) * scale,
  };
};

const actionOfPose = (pose: string): Action => {
  const map: Record<string, Action> = {
    standing: "idle", talking: "talk", pointing: "point", shocked: "shock",
    running: "run", kneeling: "kneel", fighting: "punch", falling: "fall",
    dead: "dead", laughing: "laugh", thinking: "think", praying: "kneel",
  };
  return map[pose] ?? "idle";
};

const actionAt = (beats: ActionBeat[], id: string, frame: number, fallback: ActorState): ActorState => {
  let current = fallback;
  for (const beat of beats) {
    if (beat.at * frame > Number.MAX_SAFE_INTEGER) continue;
    const state = beat.actions.find((x) => x.id === id);
    if (state && Math.round(beat.at * frame) <= frame) current = state;
  }
  return current;
};

const faceOf = (emotion: Emotion): "none" | "eyes" | "angry" | "sad" | "happy" | "shocked" | "sly" => {
  if (emotion === "happy") return "happy";
  if (emotion === "angry") return "angry";
  if (emotion === "sad" || emotion === "dead") return "sad";
  if (emotion === "shocked") return "shocked";
  if (emotion === "sly") return "sly";
  return "eyes";
};

const accessoryOf = (accessory: string): "tie" | "scarf" | "tophat" | "crown" | "cap" | "glasses" | "none" => {
  const map: Record<string, "tie" | "scarf" | "tophat" | "crown" | "cap" | "glasses" | "none"> = {
    crown: "crown", glasses: "glasses", beard: "scarf", headwrap: "scarf",
    bow_tie: "tie", hat: "tophat", sash: "scarf", none: "none", spear: "none",
  };
  return map[accessory] ?? "none";
};

const cssBackground: Record<string, string> = {
  hut: "#D8C29C", throne: "#E8D5AD", palace: "#E7D8B9", river: "#B6D8D1",
  market: "#E8D7B7", battlefield: "#D4C0A4", street: "#E6D6BA", room: "#E7D8C1", map: "#EFE2C5",
};

const Backdrop: React.FC<{kind: string}> = ({kind}) => {
  const {width, height} = useVideoConfig();
  const ground = height * 0.80;
  const ink = "#2B2722";
  const accent = "#1FA2A6";
  return (
    <AbsoluteFill style={{background: cssBackground[kind] ?? "#F7F1E3"}}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        <rect x={0} y={ground} width={width} height={height-ground} fill="#CBB58E" opacity={0.78}/>
        {kind === "river" && <>
          <path d={`M0 ${ground-95} Q${width*.24} ${ground-145} ${width*.48} ${ground-85} T${width} ${ground-110} L${width} ${ground+10} L0 ${ground+10} Z`} fill="#75B8B1"/>
          {[0.12,0.34,0.56,0.78].map((x) => <path key={x} d={`M${width*x} ${ground-30} q${width*.045} -12 ${width*.09} 0`} fill="none" stroke="#E7F4F0" strokeWidth={Math.max(2,width*.003)} />)}
        </>}
        {(kind === "palace" || kind === "throne") && <>
          <rect x={width*.06} y={height*.18} width={width*.88} height={ground-height*.18} fill="#E5D0A5" stroke={ink} strokeWidth={Math.max(2,width*.003)}/>
          {[.12,.31,.69,.88].map((x) => <g key={x}><rect x={width*x} y={height*.19} width={width*.035} height={ground-height*.19} fill="#FAEAC9" stroke={ink} strokeWidth={Math.max(2,width*.002)}/><path d={`M${width*(x-.015)} ${height*.19} q${width*.032} -${height*.07} ${width*.065} 0`} fill="#F5E3BC" stroke={ink} strokeWidth={Math.max(2,width*.002)}/></g>)}
          {kind === "throne" && <g>
            {/* A readable empty royal chair: tall back, seat, armrests, legs and a broad foot. */}
            <rect x={width*.414} y={ground-height*.49} width={width*.172} height={height*.34} rx={24} fill="#8E4F2E" stroke={ink} strokeWidth={6}/>
            <rect x={width*.433} y={ground-height*.465} width={width*.134} height={height*.285} rx={18} fill="#A96835" stroke="#D9AD66" strokeWidth={5}/>
            <path d={`M${width*.45} ${ground-height*.43} Q${width*.5} ${ground-height*.49} ${width*.55} ${ground-height*.43}`} fill="none" stroke="#E0A526" strokeWidth={6}/>
            <rect x={width*.39} y={ground-height*.285} width={width*.22} height={height*.075} rx={12} fill="#8E4F2E" stroke={ink} strokeWidth={6}/>
            <rect x={width*.405} y={ground-height*.278} width={width*.19} height={height*.055} rx={10} fill="#D0A05D" stroke="#F0CE8D" strokeWidth={4}/>
            <rect x={width*.372} y={ground-height*.345} width={width*.036} height={height*.105} rx={10} fill="#8E4F2E" stroke={ink} strokeWidth={6}/>
            <rect x={width*.592} y={ground-height*.345} width={width*.036} height={height*.105} rx={10} fill="#8E4F2E" stroke={ink} strokeWidth={6}/>
            <rect x={width*.425} y={ground-height*.22} width={width*.026} height={height*.18} fill="#6F3F29" stroke={ink} strokeWidth={5}/>
            <rect x={width*.549} y={ground-height*.22} width={width*.026} height={height*.18} fill="#6F3F29" stroke={ink} strokeWidth={5}/>
            <rect x={width*.397} y={ground-height*.045} width={width*.206} height={height*.035} rx={8} fill="#8E4F2E" stroke={ink} strokeWidth={5}/>
          </g>}
        </>}
        {(kind === "hut" || kind === "market" || kind === "street") && <>
          {[.06,.68].map((x) => <g key={x}><rect x={width*x} y={ground-height*.28} width={width*.24} height={height*.27} fill="#B88D5B" stroke={ink} strokeWidth={Math.max(2,width*.003)}/><path d={`M${width*(x-.02)} ${ground-height*.28} L${width*(x+.12)} ${ground-height*.43} L${width*(x+.26)} ${ground-height*.28} Z`} fill="#915E3B" stroke={ink} strokeWidth={Math.max(2,width*.003)}/><rect x={width*(x+.09)} y={ground-height*.18} width={width*.06} height={height*.17} fill="#4A3425"/></g>)}
          {kind === "market" && <g><path d={`M${width*.36} ${ground-height*.23} L${width*.64} ${ground-height*.23} L${width*.60} ${ground-height*.39} L${width*.40} ${ground-height*.39} Z`} fill="#1FA2A6" stroke={ink} strokeWidth={4}/><line x1={width*.38} y1={ground-height*.23} x2={width*.36} y2={ground-height*.03} stroke={ink} strokeWidth={5}/><line x1={width*.62} y1={ground-height*.23} x2={width*.64} y2={ground-height*.03} stroke={ink} strokeWidth={5}/></g>}
        </>}
        {kind === "battlefield" && <>
          <path d={`M0 ${ground-height*.28} Q${width*.25} ${ground-height*.40} ${width*.5} ${ground-height*.25} T${width} ${ground-height*.32} L${width} ${ground} L0 ${ground} Z`} fill="#B59A79"/>
          {[.08,.18,.82,.92].map((x,i)=><g key={x}><line x1={width*x} y1={ground-height*.52} x2={width*x} y2={ground} stroke={ink} strokeWidth={5}/><path d={`M${width*x} ${ground-height*.52} L${width*(x+.08)} ${ground-height*.48} L${width*x} ${ground-height*.43} Z`} fill={i%2 ? "#C0392B" : "#E0A526"}/></g>)}
        </>}
        {kind === "map" && <>
          <path d={`M${width*.14} ${height*.28} L${width*.36} ${height*.20} L${width*.57} ${height*.30} L${width*.79} ${height*.23} L${width*.88} ${height*.53} L${width*.66} ${height*.69} L${width*.4} ${height*.64} L${width*.18} ${height*.54} Z`} fill="#D5BE91" stroke="#8D7652" strokeWidth={4}/>
          <path d={`M${width*.25} ${height*.35} C${width*.40} ${height*.42} ${width*.53} ${height*.34} ${width*.69} ${height*.52}`} fill="none" stroke={accent} strokeWidth={Math.max(3,width*.005)} strokeDasharray="10 10"/>
        </>}
        <line x1={0} y1={ground} x2={width} y2={ground} stroke={ink} strokeWidth={Math.max(2,width*.002)} opacity={0.45}/>
      </svg>
    </AbsoluteFill>
  );
};

const SceneProp: React.FC<{name: string; scale: number}> = ({name, scale}) => {
  const ink = "#2B2722";
  if (name === "coin") return <Coin sym="$" r={34*scale}/>;
  if (name === "crown") return <g><polygon points="-42,28 -42,-20 -18,0 0,-34 18,0 42,-20 42,28" fill="#E0A526" stroke={ink} strokeWidth={4}/><rect x="-42" y="28" width="84" height="15" fill="#E0A526" stroke={ink} strokeWidth={4}/></g>;
  if (name === "table") return <g><rect x="-155" y="-38" width="310" height="28" rx="5" fill="#9A683C" stroke={ink} strokeWidth={6}/><rect x="-130" y="-10" width="24" height="92" fill="#80542F" stroke={ink} strokeWidth={5}/><rect x="106" y="-10" width="24" height="92" fill="#80542F" stroke={ink} strokeWidth={5}/><line x1="-150" y1="-22" x2="150" y2="-22" stroke="#C18B55" strokeWidth={5}/></g>;
  if (name === "chest") return <g><rect x="-82" y="-34" width="164" height="84" rx="8" fill="#A96835" stroke={ink} strokeWidth={6}/><path d="M-82 -34 Q0 -96 82 -34" fill="#C78B4B" stroke={ink} strokeWidth={6}/><rect x="-10" y="-20" width="20" height="35" rx="3" fill="#E0A526" stroke={ink} strokeWidth={4}/><circle cx="0" cy="-3" r="5" fill={ink}/></g>;
  if (name === "scroll") return <Box label="SCROLL" color="#F6EAD0"/>;
  if (name === "fire" || name === "torch") return <g><path d="M-22,40 L0,-34 L22,40 Z" fill="#C0392B" stroke={ink} strokeWidth={4}/><path d="M-10,25 L0,-15 L10,25 Z" fill="#E0A526"/><line x1="0" y1="35" x2="0" y2="72" stroke={ink} strokeWidth={8}/></g>;
  if (name === "sword" || name === "spear") return <g><line x1="-26" y1="42" x2="26" y2="-54" stroke={ink} strokeWidth={8} strokeLinecap="round"/><path d="M12,-30 L42,-77 L6,-52 Z" fill="#BCC5C8" stroke={ink} strokeWidth={3}/><line x1="-35" y1="24" x2="-8" y2="38" stroke="#E0A526" strokeWidth={8}/></g>;
  if (name === "shield") return <Shield/>;
  if (name === "barrel") return <Barrel label="SUPPLY"/>;
  if (name === "gold") return <GoldBar label="GOLD"/>;
  if (name === "money") return <Bill sym="$"/>;
  if (name === "chain") return <Chain x1={-42} y1={0} x2={42} y2={0} t={1}/>;
  return <Flag colors={["#C0392B","#E0A526","#1FA2A6"]}/>;
};

type Props = {scene: StorySceneData; cast: CastMember[]; title: string};

export const StoryScene: React.FC<Props> = ({scene, cast, title}) => {
  const f = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const scale = width > height ? Math.max(width / 1920, height / 600) : height / 1080;
  const ground = height * 0.80;
  const hipY = groundY(scale, ground);
  const duration = Math.max(1, scene.duration_frames);
  const progress = Math.max(0, Math.min(1, f / duration));
  const camera = scene.camera ?? "static";
  const camZ = camera === "push_in" ? 1 + 0.055 * progress : 1;
  const camX = camera === "pan_left" ? -width * 0.05 * progress :
               camera === "pan_right" ? width * 0.05 * progress : 0;
  const actors = scene.characters_on_screen ?? [];
  const beats: ActionBeat[] = (scene.action_beats ?? []).map((beat) => ({
    ...beat,
    actions: beat.actions.map((action) => ({...action})),
  }));
  const fallbackState = (actor: typeof actors[number]): ActorState => ({
    id: actor.id, action: actionOfPose(actor.pose), x: actor.x,
    emotion: (actor.expression as Emotion) ?? "neutral",
  });
  // Scene-specific blocking keeps the action physically connected to the visible prop.
  // Tables are staged at x=.50 and chests at x=.86 in this renderer.
  const primaryId = actors[0]?.id;
  const narrationLower = (scene.narration ?? "").toLowerCase();
  const moveActorAt = (targetAt: number, x: number, action: Action, emotion: Emotion) => {
    if (!primaryId || beats.length === 0) return;
    const beat = beats.reduce((best, item) =>
      Math.abs(item.at - targetAt) < Math.abs(best.at - targetAt) ? item : best, beats[0]);
    const actor = actors.find((item) => item.id === primaryId);
    const state = beat.actions.find((item) => item.id === primaryId);
    const replacement: ActorState = state ?? {
      id: primaryId, action, x: actor?.x ?? x, emotion,
    };
    Object.assign(replacement, {action, x, emotion});
    if (!state) beat.actions.push(replacement);
  };
  if (scene.props?.includes("chest") &&
      /\b(chest)\b/.test(narrationLower) &&
      /\b(beside|near|approach|approached|crouch|crouched|inspect|inspected|search|searched|check|checked)\b/.test(narrationLower)) {
    moveActorAt(0.42, 0.76, "crouch", "neutral");
  }
  if (scene.props?.includes("table") &&
      /\b(table)\b/.test(narrationLower) &&
      /\b(under|beneath|below)\b/.test(narrationLower)) {
    const hasReveal = /\b(found|finds|discovered|revealed|uncovered|retrieved|lifted)\b/.test(narrationLower);
    const action: Action = hasReveal && /\b(lifted|retrieved|held|holding)\b/.test(narrationLower)
      ? "hold" : hasReveal ? "point" : "crouch";
    moveActorAt(0.79, 0.50, action, hasReveal ? "happy" : "neutral");
  }
  const beatState = (id: string, atFrame: number): ActorState => {
    const fallbackActor = actors.find((actor) => actor.id === id) ?? {id, x: 0.5, pose: "standing", expression: "neutral"};
    const fallback = fallbackState(fallbackActor);
    let current = fallback;
    const absolute = Math.max(0, atFrame);
    for (const beat of beats) {
      const beatFrame = Math.round(beat.at * Math.max(1, duration - 1));
      if (beatFrame > absolute) break;
      const next = beat.actions.find((action) => action.id === id);
      if (next) current = next;
    }
    return current;
  };
  const keysFor = (actor: typeof actors[number]): Key[] => {
    const actorStates = beats
      .map((beat) => ({at: beat.at, state: beat.actions.find((action) => action.id === actor.id)}))
      .filter((item): item is {at: number; state: ActorState} => Boolean(item.state));
    if (!actorStates.length) actorStates.push({at: 0, state: fallbackState(actor)});
    if (actorStates[0].at > 0) actorStates.unshift({at: 0, state: fallbackState(actor)});
    return actorStates.map(({at, state}) => {
      const x = Math.max(0.08, Math.min(0.92, state.x)) * width;
      const p = poseFor(state.action, x, hipY, scale);
      return {f: Math.min(duration - 1, Math.round(at * Math.max(1, duration - 1))), p};
    });
  };
  const facingFor = (id: string, t: number): 1 | -1 => {
    const state = beatState(id, t);
    if (state.action === "walk" || state.action === "run") {
      const next = beats.map((beat) => ({at: Math.round(beat.at * Math.max(1, duration - 1)), state: beat.actions.find((x) => x.id === id)}))
        .find((item) => item.state && item.at > t)?.state;
      if (next && next.x !== state.x) return next.x > state.x ? 1 : -1;
    }
    const others = actors.filter((actor) => actor.id !== id);
    if (others.length) return others[0].x >= state.x ? 1 : -1;
    return state.x < 0.5 ? 1 : -1;
  };
  const motion = (id: string) => (t: number, p: Pose): Pose => {
    const state = beatState(id, t);
    if (state.action === "walk") return addPose(p, walkCycle(t, 0.25), true);
    if (state.action === "run") return addPose(p, runCycle(t, 0.5, 0.8), true);
    if (state.action === "talk") {
      const swing = Math.sin(t * 0.22);
      return addPose(p, {la1: -36 + 9*swing, la2: -26, ra1: 42 - 10*swing, ra2: 24, head: Math.sin(t*.13)*3}, true);
    }
    return addPose(p, idle(t, id.length), false);
  };
  const fxStart = Math.round(duration * 0.53);
  const headline = scene.title || scene.emphasis_words?.[0] || scene.narration.split(/[.!?]/)[0]?.split(/\s+/).slice(0,5).join(" ") || title;

  return (
    <Stage
      bg={<Backdrop kind={scene.background ?? "room"} />}
      hits={scene.effects?.includes("shake") ? [fxStart] : []}
      power={scene.effects?.includes("shake") ? 20 : 10}
      cam={{x: camX, y: 0, z: camZ}}
      svg={
        <>
          {scene.props?.map((name, i) => {
            const hasTable = scene.props.includes("table");
            const throneCrown = name === "crown" && scene.background === "throne";
            const coinUnderTable = name === "coin" && hasTable;
            const crownUnderTable = name === "crown" && hasTable &&
              /\b(under|beneath|below)\b.*\btable\b/.test(narrationLower);
            const defaultX = width * (0.5 + (i - (scene.props.length - 1) / 2) * 0.18);
            const x = name === "table" ? width * 0.5
              : name === "chest" ? width * 0.86
              : throneCrown || coinUnderTable || crownUnderTable ? width * 0.5
              : defaultX;
            const y = name === "table" ? ground - 100 * scale
              : name === "chest" ? ground - 48 * scale
              : throneCrown ? ground - height * 0.37
              : crownUnderTable ? ground - 34 * scale
              : coinUnderTable ? ground - 8 * scale
              : ground - 64 * scale;
            let propOpacity = 1;
            const revealable = ["crown", "coin", "scroll", "gold", "money"].includes(name);
            const hasDiscovery = /\b(found|finds|discovered|revealed|uncovered|retrieved|lifted)\b/.test(narrationLower);
            const isHeldLater = beats.some((beat) => beat.actions.some((item) =>
              item.action === "hold" || item.action === "carry"));
            if (revealable && hasDiscovery) {
              propOpacity = interpolate(f, [duration * 0.32, duration * 0.43], [0, 1],
                {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
              if (isHeldLater) {
                propOpacity *= interpolate(f, [duration * 0.62, duration * 0.70], [1, 0],
                  {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
              }
            }
            const propScale = name === "table" ? scale
              : name === "chest" ? scale * 0.9
              : throneCrown ? scale * 0.8
              : crownUnderTable ? scale * 0.65
              : coinUnderTable ? scale * 0.55
              : scale * 0.8;
            return (
              <At key={`${scene.id}-prop-${name}-${i}`} x={x} y={y} s={propScale} o={propOpacity}>
                <SceneProp name={name} scale={1}/>
              </At>
            );
          })}
          {actors.map((actor) => {
            const state = beatState(actor.id, f);
            const char = cast.find((member) => member.id === actor.id);
            const isHolding = state.action === "hold" || state.action === "carry";
            const heldProp = isHolding && scene.props?.length ? scene.props[0] : "";
            const draw = scene.effects?.includes("fade")
              ? interpolate(f, [0, Math.min(12, duration)], [0, 1], {extrapolateLeft:"clamp", extrapolateRight:"clamp"})
              : scene.effects?.includes("pop_in") ? interpolate(f, [0, 7], [0,1], {extrapolateLeft:"clamp", extrapolateRight:"clamp"}) : 1;
            return (
              <Actor key={actor.id} f={f} keys={keysFor(actor)} color={char?.accent ?? "#1FA2A6"}
                base={{x: actor.x*width, y: hipY}} scale={scale}
                facing={(t) => facingFor(actor.id, t)}
                accessory={accessoryOf(char?.accessory ?? "none")}
                face={faceOf(state.emotion)}
                behind={() => <ellipse cx={state.x * width} cy={ground - 2 * scale}
                  rx={28 * scale} ry={5 * scale} fill="#2B2722" opacity={0.14}/>}
                label={scene.show_character_labels && actors.length <= 2 ? char?.name : undefined}
                mod={motion(actor.id)} trails={state.action === "run" || state.action === "fall"}
                draw={draw}>
                {(j: Joints) => heldProp ? (
                  <At x={(j.lHand[0]+j.rHand[0])/2} y={(j.lHand[1]+j.rHand[1])/2} s={scale*0.45}>
                    <SceneProp name={heldProp} scale={1}/>
                  </At>
                ) : null}
              </Actor>
            );
          })}
        </>
      }
      html={
        <>
          <Slam f={f} at={6} out={Math.min(64, duration-10)} x={width/2} y={height * (height > width ? 0.16 : 0.13)}
            size={height>width?54:72} color="#2B2722" font="'Barlow Condensed'" weight={900} spacing={2}>
            {headline.toUpperCase()}
          </Slam>
        </>
      }
    />
  );
};
