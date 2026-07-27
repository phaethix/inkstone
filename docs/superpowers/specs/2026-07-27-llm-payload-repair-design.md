# Design: LLM payload repair + soft-drop (approach B)

**Date:** 2026-07-27  
**Approved:** Approach B

## Problem

Agnes tool-call JSON drifts into shapes that abort `StoryElements` /
`Storyboard` validation (fused keys like `name："四·二八”…`, missing required
`name` / `panel_id`, one bad list item failing the whole chunk).

## Design

1. **Shared repair** in `core/schemas.py`:
   - `repair_fused_keys(dict, field_names)` — split `field[:：]rest` keys
   - `ensure_str_field(dict, field, aliases, default)` — fill missing required strings
2. **Model hooks:** `CharacterAsset`, `Setting`, `Panel` call repair before
   field validation (keep existing alias / unnamed fallbacks).
3. **List soft-drop:** `coerce_model_list(value, model_cls)` repairs then
   `model_validate`s each item; unrecoverable items are logged and dropped.
   Used by `StoryElements.characters/settings` and `Storyboard.panels`.
4. **Screenwriter:** validate via the models only (list soft-drop is enough);
   if the top-level payload is still invalid, re-raise (operational), do not
   silently invent empty storyboards when the whole blob is garbage.

## Non-goals

- Changing Agnes prompts beyond existing lettering/name reminders
- Auto-retrying the chat call on validation failure
