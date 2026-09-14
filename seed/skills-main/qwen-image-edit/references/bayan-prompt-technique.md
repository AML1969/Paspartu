# Bayan (баян) vs Accordion — Prompt Technique

## Problem

Qwen models (both image_generate and image-edit) are heavily biased toward **piano accordions**
(the most common variant in training data). When asked to draw/replace with a bayan,
they consistently draw a piano accordion with piano keys on the right side —
even when explicitly told "NO piano keys."

5 consecutive attempts with explicit negations failed. The model ignores:
- "NO piano keyboard"
- "absolutely zero piano-style keys"
- "only round buttons, no piano keys"
- Negative prompts like "piano keyboard, piano keys"

## Solution: Positive-Only Description

Describe the target object **entirely in positive terms**, without ANY mention
of the common variant or what should NOT be there. Make the two sides
sound identical so the model has no room to insert piano keys.

### Winning prompt (qwen-image-edit)

```
Replace the musical instrument with a RED bayan that is TWICE as large.
The bayan has a rectangular box-shaped deep red lacquered body.
Both the right end and the left end of the instrument look identical —
each end is entirely covered in dense rows of small round white and black
buttons arranged in a honeycomb hexagonal grid. The two ends are mirror
images of each other: only round buttons on both ends, rows and rows of them,
like two typewriter keyboards. Wide black pleated bellows in the center.
```

### Winning prompt (image_generate — if generating from scratch)

Same principle: describe both sides identically, never mention piano.

## Key Visual Differences (Bayan vs Piano Accordion)

| Feature | Bayan (баян) | Piano Accordion |
|---------|-------------|-----------------|
| Body shape | Rectangular box | Often curved/violin-shaped |
| Right hand | Round buttons in hexagonal grid (5-6 rows) | Piano keyboard (white+black keys) |
| Left hand | Round buttons (Stradella bass) | Round buttons (Stradella bass) |
| Both sides | Look similar — buttons on both ends | Look different — piano vs buttons |

## General Principle

When replacing with a **less common variant** of an object category:
1. Never mention the common variant in the prompt
2. Never use negative constraints ("no X", "not Y")
3. Describe ONLY what the target IS, in vivid positive detail
4. Make distinguishing features sound like the defining normal, not exceptions

## Two-Step Workflow (when face + instrument both matter)

When the user wants BOTH a specific person's face AND a specific instrument/object
that the model resists drawing:

**Step 1 — Fix the instrument on the scene photo** (the one with correct setting/composition,
even if the face is wrong). Use the positive-only bayan prompt above.

**Step 2 — Fix the face on the Step 1 result** with a detailed face description:
```
Completely replace the boy's face and head with a different boy.
The new boy has: fair skin with very dense dark freckles scattered
across the entire face — cheeks, nose bridge, forehead, and chin,
dark brown almond-shaped eyes, short dark brown hair neatly parted
on the side and swept across the forehead, a warm gentle smile with
slightly parted lips showing the upper front teeth which have a small
visible gap between them, a small rounded nose, light brown subtle
eyebrows, slightly protruding ears visible at the sides.
Keep everything else exactly unchanged: the red bayan instrument,
shirt, pants, chair, background, lighting, composition.
```

This order works better than the reverse (face-first → instrument) because
the model already has the correct scene context from Step 1 and only needs
to swap the face — it won't revert to the wrong instrument.
