"""Unit tests for the pure helpers in the Textual UI module.

The interactive screens themselves aren't driven here (that wants Textual's
async pilot harness); this just pins the search-filter logic behind the
`/` favorite picker, which is plain data-in/data-out.
"""

from rich.cells import cell_len

from vodscout.ui import _ellipsize, _match_emotes

ITEMS = [("KEKW", 1203), ("PogChamp", 842), ("LULW", 611), ("kekw", 12)]


def test_empty_query_matches_everything_in_order():
    assert _match_emotes(ITEMS, "") == ITEMS
    assert _match_emotes(ITEMS, "   ") == ITEMS


def test_substring_is_case_insensitive():
    # "kek" matches both KEKW and kekw, original (most-used-first) order kept.
    assert _match_emotes(ITEMS, "kek") == [("KEKW", 1203), ("kekw", 12)]
    assert _match_emotes(ITEMS, "KEK") == [("KEKW", 1203), ("kekw", 12)]


def test_partial_matches_interior_substring():
    assert _match_emotes(ITEMS, "champ") == [("PogChamp", 842)]


def test_no_match_returns_empty():
    assert _match_emotes(ITEMS, "zzz") == []


def test_ellipsize_leaves_short_titles_untouched():
    assert _ellipsize("Just chatting", 40) == "Just chatting"
    # A title exactly at the budget still fits — no ellipsis.
    assert _ellipsize("abcde", 5) == "abcde"


def test_ellipsize_cuts_long_titles_with_ellipsis():
    out = _ellipsize("Ranked grind to Radiant — day 3", 12)
    assert out.endswith("…")
    assert cell_len(out) <= 12


def test_ellipsize_stays_within_budget_for_wide_chars():
    # Emoji are two cells wide; truncation must count cells, not characters, or
    # the cell overflows its column and the table scrolls sideways again.
    title = "😀😀😀😀😀😀 big stream"
    for width in range(1, 20):
        assert cell_len(_ellipsize(title, width)) <= width


def test_ellipsize_degenerate_widths():
    assert _ellipsize("anything", 0) == ""
    assert _ellipsize("anything", 1) == "…"
