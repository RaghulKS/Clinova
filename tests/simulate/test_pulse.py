from clinova.cohort.generate import generate_cohort
from clinova.contracts import ParsedProtocol
from clinova.simulate.pulse import (
    PatientPost,
    PatientRound,
    SitePost,
    SiteRound,
    Synthesis,
    Theme,
    pick_voices,
    render,
)


def test_render_counts_come_from_posts_not_the_model():
    voices = pick_voices(generate_cohort(60, 3), 3)
    assert len({v.persona_id for v in voices}) == 3
    posts = [PatientPost(speaker_id=v.persona_id, stance=s, burdens=b, text=f"post {i}")
             for i, (v, s, b) in enumerate(zip(voices, ["would_join", "would_not_join", "would_not_join"],
                                               [["travel"], ["travel", "cost"], []], strict=True))]
    md = render("NCT00000001", ParsedProtocol(trial_id="NCT00000001"), voices, PatientRound(posts=posts),
                SiteRound(posts=[SitePost(site_id="site-rural", text="hard", suggestions=["telehealth"])]),
                Synthesis(headline="H", themes=[Theme(theme="T", summary="S", mitigation="M")], open_questions=["Q"]),
                "m", "2026-10-11")
    assert "| would join | 1 |" in md and "| would not join | 2 |" in md and "| unsure | 0 |" in md
    assert "travel 2, cost 1" in md
    assert "MiroFish was not run" in md and "rural hospital" in md
