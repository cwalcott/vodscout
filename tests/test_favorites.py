from vodchat import favorites


def test_load_missing_returns_empty(tmp_path):
    assert favorites.load("shroud", tmp_path) == set()


def test_save_then_load_roundtrips(tmp_path):
    (tmp_path / "shroud").mkdir()
    favorites.save({"KEKW", "LULW"}, "shroud", tmp_path)
    assert favorites.load("shroud", tmp_path) == {"KEKW", "LULW"}


def test_save_creates_streamer_dir(tmp_path):
    # No prior streamer dir — save should create it.
    favorites.save({"Pog"}, "newstreamer", tmp_path)
    assert favorites.load("newstreamer", tmp_path) == {"Pog"}


def test_save_empty_clears(tmp_path):
    (tmp_path / "shroud").mkdir()
    favorites.save({"KEKW"}, "shroud", tmp_path)
    favorites.save(set(), "shroud", tmp_path)
    assert favorites.load("shroud", tmp_path) == set()


def test_load_ignores_corrupt_file(tmp_path):
    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "favorites.json").write_text("not json{")
    assert favorites.load("shroud", tmp_path) == set()
