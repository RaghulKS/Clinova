# Deck

`clinova.pptx`, `clinova.pdf`, `outline.md`, `img/` and `render/` are all generated. Do not edit them by hand.

```bash
# 1. Demo backtest numbers (needs the database and the frozen model; refuses fixture data)
uv run python docs/deck/collect_demo.py
# 2. Deck, outline, charts; --render needs LibreOffice (soffice) for the PDF and PNG renders
uv run --with python-pptx --with pymupdf python docs/deck/build_deck.py --render --final
```

Every number comes from `docs/metrics.json`, `docs/replay.json`, `docs/deck/data/demo.json`,
`docs/gate1_e2e.json`, `docs/demo_trial.md` or the cited table in `docs/ARCHITECTURE.md` section 2.
A missing source renders as `[TBD: ...]` and stamps the slide DRAFT; `--final` fails if any remain.
