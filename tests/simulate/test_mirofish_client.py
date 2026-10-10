from clinova.simulate import mirofish_client as m


def test_not_run_state(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PULSE_DIR", tmp_path)
    assert m.load_pulse("NCT0")["status"] == "not_run"
    m.save_pulse("NCT0", "burden")
    assert m.load_pulse("NCT0")["report"] == "burden"


def test_seed_pack():
    s = m.build_seed_pack("a", "b", "c", [{"age": 70, "sex": "F", "occupation": "x"}])
    assert "70y F" in s
