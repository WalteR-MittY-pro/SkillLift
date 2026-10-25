---
name: keyframe-template-counting
description: Extract ordered video keyframes, grayscale them in place, and count multiple template classes.
---

# keyframe-template-counting

## Workflow
1. Inspect video duration and frame rate, then define a deterministic keyframe policy appropriate to the task (fixed interval or scene-change threshold). Keep capture timestamps and assign sequential zero-padded filenames in timeline order.
2. Convert saved frames to single-channel grayscale and overwrite the frame files. Convert templates to grayscale in memory with compatible scaling. Confirm each written frame actually has one channel.
3. For each object class and frame, use normalized template matching or feature matching. Calibrate a confidence threshold against representative frames; handle scale variation with a small scale pyramid. Apply nonmaximum suppression so overlapping detections of one object count once.
4. Visually inspect a sample of low-confidence and crowded frames to detect false positives, occlusion, and sprite changes. Generate one CSV row per extracted frame with the required columns and original filename path. Verify counts are nonnegative integers and rows are timeline sorted.

## Example
    score = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    detections = non_max_suppression(score >= threshold)

Template matching cannot prove recall for changing object sprites; note this when reporting quality.
