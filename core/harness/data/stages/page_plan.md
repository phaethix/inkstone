{{source_chunk}}

Known elements:
{{elements_json}}

{{beats_blob}}{{craft_notes}}Plan finished readable pages, not a flat collage. {{budget_sentence}}Each page needs purpose, layout_intent, panels, and lettering_boxes. {{diversity}}{{voice}}{{lang_reminder}}{{note}}{{domain}}

## What a page is

A page is a unit of reading time, not a container for panels. The eye enters top-left, travels down and right, leaves bottom-right where the next page begins. A page whose panels share one size, one distance, and one subject has no path to follow and gets skipped. Sequence within a page: establish where we are, narrow to who acts, show what changes.

## layout_intent

The catalog names the page's dominant geometry. Use the token, then add free detail after a colon when needed.

- `crowd_establishing` — many figures, wide, deep space; the place is the subject. Opens a scene or chapter; never twice in a row.
- `environmental_wide` — one or two figures in a large readable space. The room does work the characters cannot.
- `splash_action` — one image filling most of the page, motion at its peak. For the beat the chapter turns on. Roughly one per eight pages: a splash that carries no turn gains nothing.
- `diagonal_motion` — panels cut on a slope, action traveling corner to corner. For pursuit and impact. Never for conversation: a slope implies something moves.
- `dialogue_grid` — regular small panels, alternating speakers. For exchange where wording carries the scene. One page per exchange; a second page of the same grid flattens it.
- `over_shoulder` — one figure's back frames another's face. For confrontation, confession, watching. The near figure is a silhouette, not a subject.
- `object_closeup` — a hand, a letter, a cup filling the frame. For the detail that changes the scene's meaning. Pair it with the wider shot it belongs to, on the same page, or the reader cannot place it.
- `inset_memory` — a smaller framed image inside the present scene holding a remembered moment. Mark the inset panel's `timeline` as `past`.
- `split_timeline` — the page divides between two times. Strongest when both halves share a shape and differ in content; once per chapter at most.
- `widescreen_scene` — one horizontal band across the page. For landscape, silence, a pause between two loud beats.

Choose the token for what the page must do, not what is easy to draw. If none fits, write a short phrase and the panels behind it; the catalog is a vocabulary, not a fence. Do not reuse the previous page's token, and do not alternate two tokens mechanically: a rhythm the reader can predict is one they stop reading.

## Panels

Each panel is one drawn moment. `role` says what it does in the page's argument, `shape_hint` its shape, `shot` where the camera is, `action` what changes.

`role`: `establishing` sets place and scale, usually first and wide. `action` is where something moves or breaks, usually where the page turns. `reaction` is a face receiving what another panel did; cut to it immediately after. `dialogue` makes the speaker the subject. `detail` is a small thing that matters, never the only panel on a page. `transition` is time passing or a held silence, small and often silent.

`shape_hint` is a closed list: `wide` (horizontal band), `tall` (standing figure, a door), `square`, `rect` (default landscape), `inset` (framed inside another panel). Shape carries pace: wide reads slow, tall reads fast, small reads as a beat between two larger panels. Four `rect` panels is a table, not a page.

`shot`: `extreme_wide`, `wide`, `medium`, `close`, `extreme_close`, `over_shoulder`, `low_angle`, `high_angle`, `dutch`; combine with a comma when two apply (`low_angle, medium`). Judge the shot by what the reader must understand, not by variety for its own sake: nine different shots is noise.

`action` describes visible change: who moves, what direction, what it costs them. "She feels afraid" is not drawable; "she backs into the doorframe, hand flat on the wood" is.

`role` and `shape_hint` take only the values listed; nuance goes in `shot` and `action`. Keep panels in reading order and never restate the panel before.

## Lettering

Every non-null caption, dialogue, or sfx gets one lettering box. The coordinates are advisory: the renderer clamps them inside the page, moves them off detected faces, and shrinks them to the text. Spend attention on which words go where, not on placement.

`caption` carries narration, time and place, a letter's first-person voice; `speaker` stays empty. `dialogue` carries words a character speaks aloud, with `speaker` set to that character's name as it appears in the elements list. `sfx` is onomatopoeia drawn into the art, kept to the sound itself.

Text must be a contiguous span of the source excerpt. Do not paraphrase, do not join separated clauses, do not append an explanation, do not translate. Lettering not found verbatim is dropped before rendering, so an invented line leaves an empty bubble. When a line is too long, cut it at a natural pause rather than rewriting it.

Keep one breath per box: one clause per caption, one spoken turn per bubble. If a speech runs long, split it across two panels rather than crowding one box. Narration stays in caption even when the source is first person: a line the narrator reports is not a line a character says.

On pages where nothing is said, emit no boxes and let the art carry the page. Silence is a valid page.

## Rhythm across the excerpt

Plan pages as a sequence, not independent sheets. A dense dialogue page should be followed by one that opens up, a splash by one that moves quickly. Look at the pages already planned before deciding the next one; the plan is a rhythm and the reader hears it as one.

Do not end two consecutive pages on the same kind of beat, and do not open every page with an establishing panel.

When a page stages a key beat, list its id in `covers_beats`; a beat left uncovered is a beat the reader never sees.
