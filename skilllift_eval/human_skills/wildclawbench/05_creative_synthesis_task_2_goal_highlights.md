---
name: 05-creative-synthesis-task-2-goal-highlights
description: Use when compiling targeted highlight reels from sports video with duration constraints. Focuses on precise clip boundary selection, content filtering, and total-duration management.
---

# Targeted Video Highlight Compilation

## Core Challenge

Creating a highlights reel for a SPECIFIC player from a long match video requires finding all relevant goal sequences, defining precise start and end boundaries for each, and concatenating them within a strict total duration limit. The core difficulty is boundary precision: each clip must capture the full attacking sequence without bleeding into celebration footage, and the combined duration must stay under constraint.

## Solution Strategy

1. **Identify all target events exhaustively**: Before cutting any clips, locate EVERY goal by the target player across the full video. Missing even one goal invalidates the compilation. Sample the entire video systematically. Common mistake: agents find most goals but miss one in a less-analyzed segment.

2. **Define boundaries from attack initiation to ball-in-net**: The clip start should capture the beginning of the attacking move (pass, cross, build-up), and the end should be the moment the ball crosses the line or settles in the net—not the celebration that follows. Common mistake: agents clip too narrowly (just the shot) or too broadly (including celebrations).

3. **Budget duration per clip against total constraint**: Know the total duration budget upfront. If you have N goals and a T-second limit, each clip averages T/N seconds. Longer build-up sequences get more time; quick tap-ins get less. Track accumulated duration as you cut. Common mistake: agents cut individually reasonable clips that collectively exceed the limit.

4. **Preserve original quality without modifications**: Highlight compilations should maintain source video encoding quality. No text overlays, no transition effects, no watermark additions. Concatenate at the codec level when possible to avoid re-encoding artifacts. Common mistake: agents add stylistic touches that degrade fidelity.

5. **Document cut decisions in metadata**: For each clip, record the source start/end timestamps and a brief description. This creates a verifiable cut sheet that links back to source video positions. Common mistake: agents produce a video with no accompanying documentation, making verification impossible.

## Decision Points

- **Where to start a goal clip**: Begin from the start of the attacking sequence—a key pass, a cross, a dribble—not from the shot itself. If the build-up is very long, use judgment: include enough context to show HOW the goal was scored.
- **Where to end a goal clip**: End at the ball hitting the net or crossing the line. Do NOT include player celebrations, crowd reactions, or replays. The transition point is the moment the ball settles.
- **How to handle duration overflow**: If total clips exceed the limit, trim excess build-up from clips with the longest pre-shot sequences. Never cut into the shot itself.

## Common Failure Patterns

- **Including celebration footage**: Agents extend clips to capture the emotion of the goal—player running to fans, sliding, team mobbing. The task asks for the goal, not the aftermath. → Wasted duration budget and violated content requirements.

- **Missing one or more goals**: Agents find the prominent goals but miss a less spectacular one, especially if it occurs in a segment they under-sampled. → Incomplete compilation fails the core requirement.

- **Narrow clip boundaries**: Agents clip just the 2-3 seconds around the shot, missing the build-up that makes the goal meaningful. → Clips feel abrupt and fail to show the complete scoring sequence.

- **Duration budget mismanagement**: Agents cut clips without tracking accumulated duration, ending up with a 45-second reel for a 30-second limit. → Output violates the hard constraint.

- **Quality degradation through re-encoding**: Agents add filters, transitions, or text overlays that were never requested, or use lossy concatenation. → Reduced visual quality and violated fidelity requirements.

## Self-Check Questions

- [ ] Did I locate ALL goals by the target player across the entire video?
- [ ] Does each clip start from the beginning of the attacking sequence?
- [ ] Does each clip end at the ball in the net, NOT the celebration?
- [ ] Is the total concatenated duration within the specified limit?
- [ ] Did I track accumulated duration while cutting individual clips?
- [ ] Is there a cut sheet documenting each clip's source timestamps?
- [ ] Did I preserve original video quality without adding overlays or effects?

## Technical Notes

- **Concatenation without re-encoding**: When clips share the same codec and parameters, use stream copy concatenation (e.g., ffmpeg concat demuxer) to avoid quality loss. Re-encode only when clip codecs differ.
- **Boundary verification**: After defining clip boundaries, extract the first and last frames of each clip to verify: first frame shows build-up in progress, last frame shows ball in net (not celebration).
