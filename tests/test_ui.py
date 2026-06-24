"""Unit tests for the pure helpers in the Textual UI module.

The interactive screens themselves aren't driven here (that wants Textual's
async pilot harness); this just pins the search-filter logic behind the
`/` favorite picker, which is plain data-in/data-out.
"""

from vodchat.ui import _match_emotes

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
