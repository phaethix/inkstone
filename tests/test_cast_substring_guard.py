"""tests/test_cast_substring_guard.py — cast matching must not split a name.

``鸿渐`` is a substring of ``方鸿渐``. The cast repair that scans panel actions
with ``name in action`` therefore pulled a merged-away alias back into a page
that only names the canonical name, which changed the page's reference set and
made the merge-cost re-run repaint an unaffected page.
"""

from core.comic.visual_bible import ComicPagePlan, ComicPagePlanSet, enforce_page_cast
from core.schemas import PagePanelSpec


def test_alias_substring_of_canonical_name_is_not_added_back():
    # 方鸿渐 stays; its substring 鸿渐 (a merged-away alias) must not join.
    plan = ComicPagePlan(
        page_id="p1",
        panels=[PagePanelSpec(panel_id="1", action="方鸿渐 looks at the sea", characters=[])],
        reference_characters=["方鸿渐"],
    )
    guarded = enforce_page_cast(
        ComicPagePlanSet(pages=[plan]),
        known_names=["方鸿渐", "鸿渐"],
    )
    page = guarded.pages[0]
    assert page.reference_characters == ["方鸿渐"]
    assert page.panels[0].characters == ["方鸿渐"]


def test_a_distinct_known_name_is_still_added():
    plan = ComicPagePlan(
        page_id="p1",
        panels=[PagePanelSpec(panel_id="1", action="方鸿渐 hands the map to 福贵", characters=[])],
        reference_characters=["方鸿渐"],
    )
    guarded = enforce_page_cast(
        ComicPagePlanSet(pages=[plan]),
        known_names=["方鸿渐", "福贵"],
    )
    page = guarded.pages[0]
    assert sorted(page.reference_characters) == ["方鸿渐", "福贵"]
    assert sorted(page.panels[0].characters) == ["方鸿渐", "福贵"]


def test_a_name_equal_to_the_action_word_still_matches():
    plan = ComicPagePlan(
        page_id="p1",
        panels=[PagePanelSpec(panel_id="1", action="鸿渐 looks at the sea", characters=[])],
        reference_characters=[],
    )
    guarded = enforce_page_cast(
        ComicPagePlanSet(pages=[plan]),
        known_names=["鸿渐"],
    )
    page = guarded.pages[0]
    assert page.reference_characters == ["鸿渐"]
