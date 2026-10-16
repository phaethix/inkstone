"""Phase 3: extract, beats, page plan, and finished-page render keys."""

from core.comic.cas import (
    beats_action_key,
    bible_action_key,
    extract_action_key,
    page_plan_action_key,
    page_script_action_key,
    portrait_action_key,
    render_page_action_key,
    render_panel_action_key,
    storyboard_action_key,
)


def test_an_extract_key_tracks_the_chunk_text_and_not_a_page_size():
    base = extract_action_key(chunk="第一回", env="chat-a", stage_src="src")
    assert extract_action_key(chunk="第一回", env="chat-a", stage_src="src") == base
    assert extract_action_key(chunk="第二回", env="chat-a", stage_src="src") != base
    assert "page_size" not in extract_action_key.__code__.co_varnames
    assert "l3_enabled" not in extract_action_key.__code__.co_varnames


def test_a_beats_key_tracks_the_chunk_text():
    base = beats_action_key(chunk="第一回", env="chat-a", stage_src="src")
    assert beats_action_key(chunk="第一回", env="chat-a", stage_src="src") == base
    assert beats_action_key(chunk="第二回", env="chat-a", stage_src="src") != base
    assert "page_size" not in beats_action_key.__code__.co_varnames


def test_a_page_plan_key_keeps_recent_layout_strings_out():
    history = {"recent_layouts": {"limit": 8, "summarizers": {"window": "abc"}}}
    base = page_plan_action_key(
        chunk="第一回",
        beats_json="[]",
        history=history,
        env="chat-a",
        stage_src="src",
    )
    assert (
        page_plan_action_key(
            chunk="第一回",
            beats_json="[]",
            history=history,
            env="chat-a",
            stage_src="src",
        )
        == base
    )
    changed_beats = page_plan_action_key(
        chunk="第一回",
        beats_json='[{"beat_id":"b1"}]',
        history=history,
        env="chat-a",
        stage_src="src",
    )
    assert changed_beats != base
    assert "layout_intent" not in page_plan_action_key.__code__.co_varnames


def test_a_storyboard_key_tracks_the_chunk_and_its_cast():
    base = storyboard_action_key(chunk="第一回", cast="方鸿渐", env="chat-a", stage_src="src")
    assert (
        storyboard_action_key(chunk="第一回", cast="方鸿渐", env="chat-a", stage_src="src") == base
    )
    assert (
        storyboard_action_key(chunk="第二回", cast="方鸿渐", env="chat-a", stage_src="src") != base
    )
    assert (
        storyboard_action_key(chunk="第一回", cast="方鸿渐\n鸿渐", env="chat-a", stage_src="src")
        != base
    )
    assert "portrait" not in storyboard_action_key.__code__.co_varnames


def test_a_page_script_key_tracks_the_chunk_and_its_storyboard():
    base = page_script_action_key(
        chunk="第一回",
        storyboard_json='{"panels":[]}',
        elements_json="{}",
        env="chat-a",
        stage_src="src",
    )
    assert (
        page_script_action_key(
            chunk="第一回",
            storyboard_json='{"panels":[]}',
            elements_json="{}",
            env="chat-a",
            stage_src="src",
        )
        == base
    )
    assert (
        page_script_action_key(
            chunk="第二回",
            storyboard_json='{"panels":[]}',
            elements_json="{}",
            env="chat-a",
            stage_src="src",
        )
        != base
    )
    assert (
        page_script_action_key(
            chunk="第一回",
            storyboard_json='{"panels":["look"]}',
            elements_json="{}",
            env="chat-a",
            stage_src="src",
        )
        != base
    )
    assert "portrait" not in page_script_action_key.__code__.co_varnames
    assert "page_size" not in page_script_action_key.__code__.co_varnames


def test_a_portrait_key_ignores_the_reference_file():
    base = portrait_action_key(name="石猴", material="prompt", env="image-a", stage_src="src")
    assert (
        portrait_action_key(name="石猴", material="prompt", env="image-a", stage_src="src") == base
    )
    assert (
        portrait_action_key(name="石猴", material="other", env="image-a", stage_src="src") != base
    )
    assert "canonical_portrait_ref" not in portrait_action_key.__code__.co_varnames


def test_a_bible_key_tracks_the_chunk_and_not_the_cast():
    history = {"bible_summary": {"limit": 1, "summarizers": {"reconcile": "abc"}}}
    base = bible_action_key(chunk="第一回", history=history, env="chat-a", stage_src="src")
    assert bible_action_key(chunk="第一回", history=history, env="chat-a", stage_src="src") == base
    assert bible_action_key(chunk="第二回", history=history, env="chat-a", stage_src="src") != base
    assert "characters" not in bible_action_key.__code__.co_varnames
    assert "content_hash" not in bible_action_key.__code__.co_varnames


def test_a_panel_key_ignores_the_previous_panel():
    base = render_panel_action_key(
        panel_json="walk",
        style="manhua",
        page_key="c0000-p0000",
        env="image-a",
        stage_src="src",
    )
    assert (
        render_panel_action_key(
            panel_json="walk",
            style="manhua",
            page_key="c0000-p0000",
            env="image-a",
            stage_src="src",
        )
        == base
    )
    assert (
        render_panel_action_key(
            panel_json="run",
            style="manhua",
            page_key="c0000-p0000",
            env="image-a",
            stage_src="src",
        )
        != base
    )
    assert "prev_panel" not in render_panel_action_key.__code__.co_varnames
    assert "portrait_ref" not in render_panel_action_key.__code__.co_varnames


def test_a_render_key_tracks_the_plan_and_the_prompt():
    base = render_page_action_key(
        plan_json='{"page":1}',
        prompt="draw the stone",
        resolved_size="1024x1536",
        page_key="c0000-p0000",
        env="env-a",
        stage_src="src",
    )
    assert (
        render_page_action_key(
            plan_json='{"page":1}',
            prompt="draw the stone",
            resolved_size="1024x1536",
            page_key="c0000-p0000",
            env="env-a",
            stage_src="src",
        )
        == base
    )
    assert (
        render_page_action_key(
            plan_json='{"page":2}',
            prompt="draw the stone",
            resolved_size="1024x1536",
            page_key="c0000-p0000",
            env="env-a",
            stage_src="src",
        )
        != base
    )
    assert (
        render_page_action_key(
            plan_json='{"page":1}',
            prompt="draw the monkey",
            resolved_size="1024x1536",
            page_key="c0000-p0000",
            env="env-a",
            stage_src="src",
        )
        != base
    )
    assert (
        render_page_action_key(
            plan_json='{"page":1}',
            prompt="draw the stone",
            resolved_size="1024x1536",
            page_key="c0001-p0000",
            env="env-a",
            stage_src="src",
        )
        != base
    )
    assert "l2_reference" not in render_page_action_key.__code__.co_varnames
