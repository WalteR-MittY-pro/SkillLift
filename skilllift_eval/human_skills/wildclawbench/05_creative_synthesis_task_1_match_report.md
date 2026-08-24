---
name: 05-creative-synthesis-task-1-match-report
description: Use when analyzing long video to identify temporal events and producing interleaved text-video reports. Focuses on event localization, clip boundary precision, and multimodal cross-referencing.
---

# Long-Video Event Analysis and Interleaved Reporting

## Core Challenge

Analyzing a full-length sports video requires temporal event localization at minute-level precision, then producing both a textual narrative and correctly extracted video clips that align with the described events. The difficulty is threefold: identifying ALL key events (not just the obvious ones), pinpointing their exact timestamps in source video time, and cutting clips whose visual content genuinely matches the text descriptions.

## Solution Strategy

1. **Sample systematically before concluding**: Long videos can't be understood from a handful of frames. Sample at regular intervals across the ENTIRE video duration, then drill into promising segments with denser frame extraction. Common mistake: agents analyze only the first few minutes or sparse frames, missing events in later portions.

2. **Anchor timestamps to source video position**: Every event timestamp must refer to the position within the source video file, not match clock time or commentary time. Track the mapping explicitly. Common mistake: agents confuse broadcast clock time (e.g., "39th minute") with video file position, producing clips from wrong locations.

3. **Verify clip-content alignment bidirectionally**: After extracting a clip, re-examine its frames to confirm the event is actually visible. Text descriptions must match what the clip shows. Common mistake: agents trust their timestamp math without checking that the extracted clip actually contains the described action.

4. **Distinguish key events from secondary highlights**: Goals, cards, and scoring plays are key events requiring clips. Dangerous attacks, fouls, and near-misses are highlights needing text only. Classify each event type before deciding what outputs it needs. Common mistake: agents treat all notable moments equally, either missing clips for key events or wasting effort on clips for minor moments.

5. **Cross-reference scoreboard and visual cues**: Use on-screen scoreboards, jersey numbers, and visual context to verify event details (scorer identity, score changes). Don't rely on a single information source. Common mistake: agents hallucinate player names or score lines from partial visual evidence.

## Decision Points

- **Frame sampling density**: Start with 1 frame per ~30 seconds for overview, then extract at 1-2 fps around detected event clusters. If the video has fast-paced action, increase density.
- **Clip duration boundaries**: Start the clip a few seconds BEFORE the key action begins (pass/build-up) and end a few seconds AFTER the outcome is clear. Avoid cutting mid-action.
- **Event classification threshold**: If uncertain whether a moment qualifies as a "key event" vs "highlight," ask: did it change the score or result in a card? If yes, it's key. If not, it's a highlight.

## Common Failure Patterns

- **Partial video coverage**: Agents extract frames from only part of the video, missing events in uncovered segments. The later half of a video is just as important as the opening. → Missing events produce incomplete reports.

- **Timestamp displacement**: Agents use commentary-mentioned match time (e.g., "32nd minute") as the video file timestamp, but these rarely align due to pre-match content, stoppages, or replay offsets. → Clips extracted from wrong positions, showing unrelated footage.

- **Unverified clip content**: Agents extract clips based on calculated timestamps and never check the actual frames. → Clips that show celebrations, replays, or unrelated play instead of the described event.

- **Hallucinated event details**: Agents invent scorer names, assist providers, or score lines from generic match knowledge or partial visual evidence without verification. → Factually incorrect report content.

- **Inconsistent text-clip pairing**: The text describes one event but the paired clip shows something different, because they were created independently without cross-checking. → Report loses credibility when text and video disagree.

## Self-Check Questions

- [ ] Did I sample frames across the ENTIRE video duration, not just the first portion?
- [ ] Are my timestamps anchored to source video file position (not broadcast clock time)?
- [ ] Did I verify each extracted clip's frames actually show the described event?
- [ ] Did I distinguish key events (needing clips) from highlights (needing text only)?
- [ ] Are player names, score changes, and event descriptions grounded in visual evidence?
- [ ] Do the text descriptions and their paired video clips tell the same story?
- [ ] Are all key events represented in both text and video?

## Technical Notes

- **Video position vs match clock**: Broadcast match time and video file position are different coordinate systems. Map explicitly: if the match clock shows "39:00" but this occurs at 2707 seconds into the video file, use 2707 (or "45:07") as your timestamp reference.
- **Clip extraction precision**: Use seek-before-copy (`-ss` before `-i`) for fast seeking to approximate position, then verify with frame extraction. Re-extract if the clip doesn't start at the right moment.
