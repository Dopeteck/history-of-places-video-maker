# Remotion stickman renderer

This is an isolated rendering adapter for the existing Python story pipeline. It vendors the stickman animation engine from [nispa/stickman-video](https://github.com/nispa/stickman-video) at upstream commit \`c2ede0f707559bf9e3d207c3902d64bf3affe8ae\`. The upstream engine is MIT-licensed; its license notice is preserved in \`UPSTREAM_LICENSE\`.

## Why this adapter exists

- Uses keyframed SVG stick figures, walk/run cycles, poses, physical title cards, impact effects, and vector props rather than simply moving still images.
- Uses the existing Python story/chapter generator and existing CPU Kokoro narration.
- Has no Claude Code runtime requirement, no Gemini Media MCP requirement, no paid image-generation step, and no paid TTS dependency.
- Preserves the repository's source review and approval gate. This subproject only renders a story that has already been approved.
- Does not publish, schedule, or send videos anywhere.

The scripting model still depends on the configured provider's available free quota (Groq, Gemini, or OpenRouter); rendering and speech are local/free apart from runner usage and bandwidth. Remotion's license allows free use for individuals and teams of up to three people, including commercial use, subject to its terms: https://www.remotion.dev/docs/license.

## Local preview

Install Node.js 22 or later and FFmpeg. From this directory run:

\`\`\`bash
npm install
npm run typecheck
\`\`\`

The default \`src/generated/plan.json\` is a visual fixture, not a factual story. Generate narration and a plan with the root Python adapter to render a real approved draft:

\`\`\`bash
python remotion_renderer.py --smoke-test --out output/remotion-preview
\`\`\`

The smoke preview uses synthetic test audio. A production render is only run for an approved saved draft by the existing workflow.
