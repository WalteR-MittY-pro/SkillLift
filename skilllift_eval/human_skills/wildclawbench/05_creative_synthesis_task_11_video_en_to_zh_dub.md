---
name: 05-creative-synthesis-task-11-video-en-to-zh-dub
description: Use when building end-to-end video dubbing pipelines across languages. Focuses on pipeline orchestration, translation-voice alignment, audio-visual synchronization, and source video preservation.
---

# Cross-Language Video Dubbing Pipeline

## Core Challenge

Video dubbing is a multi-stage pipeline: speech recognition → translation → voice synthesis → audio-video muxing. Each stage's errors compound downstream—bad transcription yields bad translation yields bad dubbing. The core difficulty is maintaining quality across the ENTIRE chain while preserving the original video's visual track frame-for-frame. A single broken stage invalidates the whole output.

## Solution Strategy

1. **Get the transcription right before everything else**: The English transcript is the foundation. If it's wrong, the translation and dubbing built on it will be wrong too. Verify transcription accuracy against the actual audio, not just trust the ASR output. Common mistake: agents accept the first ASR result without verification, then build everything on an incorrect foundation.

2. **Translate for natural speech, not literal equivalence**: Spoken Chinese differs from written Chinese. Dubbing translation should sound natural when spoken aloud—shorter sentences, conversational register, appropriate honorifics. Literal translations sound robotic when spoken. Common mistake: agents produce a text-translation-quality output that sounds stiff as speech.

3. **Preserve speaker characteristics in voice synthesis**: The dubbed voice should match the original speaker's gender, approximate age, energy level, and speaking pace. A young energetic male speaker should not sound like a calm female narrator. Choose voice parameters deliberately. Common mistake: agents use default TTS voices without considering speaker match.

4. **Isolate video from audio completely**: The output video must have the EXACT same visual track as the source's first segment—same frames, same timing, same resolution. Only the audio track changes. Any visual modification (re-encoding artifacts, resolution changes, frame drops) is a failure. Common mistake: agents re-encode the video track during muxing, introducing subtle visual differences.

5. **Test the full pipeline end-to-end before finalizing**: After muxing, verify: (a) the video portion matches the source frame-for-frame, (b) the audio is in the target language, (c) the audio content matches the translation, (d) the duration is within bounds. Pipeline failures are often silent—the output exists but is subtly wrong. Common mistake: agents check that the file exists but don't verify its contents.

## Decision Points

- **ASR approach**: If the video has embedded subtitles, extract them directly. Otherwise, extract audio and run ASR. For lectures/presentations, also check on-screen slides for text that supplements spoken content.
- **TTS voice selection**: Match gender first (critical), then approximate age and energy. If the speaker is a middle-aged male academic, select a voice with similar characteristics. Voice mismatch is heavily penalized.
- **Audio-video muxing strategy**: Use stream copy for the video track (no re-encoding) and replace only the audio track. This preserves visual fidelity perfectly. Re-encode audio only if format compatibility requires it.

## Common Failure Patterns

- **Foundation errors**: Agents accept an imperfect ASR result and build translation and dubbing on top of it. Errors cascade: wrong transcription → wrong translation → wrong dubbing content. → The entire pipeline output is low quality.

- **Visual track modification**: Agents re-encode the video track during the muxing step, introducing compression artifacts or resolution changes. The output video doesn't match the source frame-for-frame. → Visual consistency failures, even when the dubbing audio is correct.

- **Gender-voice mismatch**: Agents use a default TTS voice without matching it to the original speaker. A male speaker gets a female voice (or vice versa). → Immediate and severe audio quality penalty.

- **Robotic TTS quality**: Agents use basic TTS that sounds mechanical—monotone delivery, unnatural pauses, wrong emphasis. The dubbing doesn't sound like a person speaking Chinese naturally. → Low naturalness scores.

- **Duration violations**: Agents don't trim to the required duration. The output video is longer than specified (e.g., the full video instead of the first two minutes). → Gating failure on duration constraint.

## Self-Check Questions

- [ ] Did I verify the ASR transcript against the actual audio (not just trust it)?
- [ ] Is the translation natural spoken Chinese (not literal/written-style)?
- [ ] Does the TTS voice match the original speaker's gender and energy?
- [ ] Did I preserve the video track using stream copy (no re-encoding)?
- [ ] Is the output video duration within the specified limit?
- [ ] Did I verify the output video's visual track matches the source frame-for-frame?
- [ ] Did I test the complete pipeline output end-to-end?

## Technical Notes

- **Video preservation during muxing**: Use `-c:v copy` (codec copy for video) when replacing audio. This preserves the exact H.264 stream from the source without re-encoding. Only the audio stream is replaced.
- **Duration trimming**: Trim the source video to the required duration BEFORE processing audio. Work with exactly the segment you need—don't process the full video then trim afterward, as this can cause audio-video desync at the cut point.
- **Audio extraction for ASR**: Extract audio as 16kHz mono WAV for best ASR compatibility. Higher sample rates or stereo channels may not improve accuracy and increase processing time.
