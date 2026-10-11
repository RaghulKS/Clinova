import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "relabel_month_starts", Path(__file__).parents[2] / "scripts" / "relabel_month_starts.py")
relabel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relabel)


def test_keyword_rules_follow_the_prompt_definition():
    assert relabel.keyword_category("Slow accrual") == "accrual"
    assert relabel.keyword_category("Poor enrollment") == "accrual"
    assert relabel.keyword_category("slow accrual and lack of additional funding") == "other"
    assert relabel.keyword_category("IDMC recommendation; sponsor closed enrollment") == "other"
    assert relabel.keyword_category("Lack of efficacy") == "other"
    assert relabel.keyword_category("  ") == "unknown"


def test_agreement_kappa_and_confusion():
    pairs = [(True, True)] * 4 + [(False, False)] * 4 + [(True, False), (False, True)]
    result = relabel.agreement(pairs)
    assert result["accuracy"] == 0.8 and result["kappa"] == 0.6
    assert result["confusion"] == {"both_accrual": 4, "both_not": 4, "keyword_only_accrual": 1, "gemini_only_accrual": 1}
    assert relabel.agreement([]) == {"n": 0}
