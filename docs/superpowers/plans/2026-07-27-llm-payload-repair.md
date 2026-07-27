# LLM payload repair + soft-drop

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fused-key / missing-required LLM payloads never abort a whole extract or storyboard when individual list items can be repaired or dropped.

**Architecture:** Shared dict repair helpers + per-model `before` validators + `coerce_model_list` soft-drop for nested lists.

**Tech Stack:** Pydantic v2, existing `coerce_*` helpers.

## Global Constraints

- Loud `logging.warning` when dropping items
- Defaults: character/setting `unnamed`; panel `panel_id` = `p{index:04d}` when possible else `panel`
- Do not swallow top-level ValidationError for non-list structural failure
- TDD; English code/comments

---

### Task 1: Shared repair helpers + unit tests

**Files:** `core/schemas.py`, `tests/test_llm_payload_repair.py`

- [ ] `repair_fused_keys`, `_unwrap_quoted_fragment`, `ensure_str_field`, `coerce_model_list`
- [ ] Tests for Chinese/ASCII colon, mixed quotes, soft-drop

### Task 2: Wire CharacterAsset / Setting / Panel

**Files:** `core/schemas.py`, `tests/test_schemas.py`

- [ ] Call shared repair from `_ensure_name` / panel id ensure
- [ ] Keep existing Setting tests green; add panel fused-key + bad-item soft-drop

### Task 3: StoryElements / Storyboard list validators

**Files:** `core/schemas.py`

- [ ] Replace `coerce_object_list` for characters/settings/panels with `coerce_model_list`

### Task 4: Verify + commit

- [ ] Full pytest + ruff
- [ ] Commit on feature branch
