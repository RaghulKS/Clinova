# Spike T02: personas, MiroFish, licenses

Verified 2026-10-10 by reading the dataset cards, HF API and GitHub pages (no data downloaded, nothing run). Items marked UNTESTED are commands I did not execute.

## Decision summary
- **MatrAIx Persona 1M: do not use.** Its license is "non-commercial research use only" (`matraix-research-only`, `license: other`); subsets, including the 400k synthetic rows, inherit it. It cannot be redistributed in an MIT repo or in our seed dump, and several upstream sources add ODbL / CC BY-SA / NORC terms. The MIT license applies to the GitHub code only.
- **Use `nvidia/Nemotron-Personas-USA` (CC BY 4.0)** as the primary persona source. Only attribution is required. It is US-grounded (Census/BLS), which fits NSCLC trials better than MatrAIx's global margins.
- Fallback of last resort: synthetic personas sampled from public census marginals (section 1.4).

## 1. Personas

### 1.1 MatrAIx Persona 8B (arXiv 2608.04205), checked and rejected
- Code: https://github.com/MatrAIx-ai/MatrAIx-Persona-8B (MIT). Data: https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M (the `_Public_Release` name redirects here). 999,847 personas (599,847 derived from real records, 400,000 synthetic), 10 zstd parquet shards, 4.17 GB.
- Format: 1,290 categorical attributes packed as 4-bit nibbles in an `attributes` binary column (645 bytes/row) plus `null_bitmap`; must decode with pyarrow against `persona_codes.schema.json`. `datasets` cannot open the shards. Rows are sparse (about 656 of 1,290 populated on average).
- Calibrated only to global UN margins (age_bracket, region, gender_identity, urbanicity), not US. Card says "not a representative sample of any population".
- Field mapping, had the license allowed it: age=`age_bracket` (brackets only); sex=`gender_identity`; race_ethnicity=`demo_ethnicity_broad`; occupation=`demo_employment_status` (status only, no job title); caregiving=`health_caregiver_status`; transport=`lstyle_commute_mode`, `health_mobility`; income band=`demo_household_income`; risk tolerance=`risk_tolerance`; health literacy=`health_health_literacy`. Also useful: `health_insurance_status`, `urbanicity`, `highest_education`, `demo_disability_status`.
- Download of a 20k sample (UNTESTED): `huggingface-cli download MatrAIx2026/MatrAIx_Persona_1M data/persona-1m-0000.parquet persona_codes.schema.json --repo-type dataset --local-dir .data/matraix`, then decode the first 20,000 rows with the card's `decode()` and write parquet. Moot given the license.

### 1.2 Chosen: nvidia/Nemotron-Personas-USA
- https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA, license CC BY 4.0 (card and HF metadata agree). Commercial use and redistribution allowed with attribution; compatible with an MIT repo (keep an attribution line). v1.1: 1M records, fully synthetic, no real people, grounded in CC0 Census/BLS aggregates, 50 states + PR + VI. Plain parquet, loads with `datasets`.
- Schema (23 columns): `uuid`, `persona`, `professional_persona`, `sports_persona`, `arts_persona`, `travel_persona`, `culinary_persona`, `cultural_background`, `skills_and_expertise`, `skills_and_expertise_list`, `hobbies_and_interests`, `hobbies_and_interests_list`, `career_goals_and_ambitions`, `sex`, `age`, `marital_status`, `education_level`, `bachelors_field`, `occupation`, `city`, `state`, `zipcode`, `country`.
- Field mapping to Clinova:

| Clinova field | Source | Notes |
| --- | --- | --- |
| age | `age` | direct |
| sex | `sex` | direct |
| race_ethnicity | none | not in dataset; sample from Census marginals by state (public domain), label as assumption |
| occupation | `occupation` | direct |
| caregiving | none | derive from age and `marital_status` using published caregiver prevalence; assumption |
| travel/transport access | `state`, `zipcode`, `city` | derive rural/urban from ZIP (RUCA/Census), distance-to-cancer-center proxy; assumption |
| income band | none | derive from `occupation`, `education_level`, `age` using BLS/ACS medians; assumption |
| risk tolerance | none | sample from a stated prior, optionally nudged by age; assumption |
| health literacy | none | derive from `education_level` using published literacy shares; assumption |

  So 4 of 9 fields are real dataset values (age, sex, occupation, geography); the rest are sourced assumptions layered on, consistent with clinical traits living in `assumptions.yaml`. Free-text persona columns can feed the agent prompt.
- 20,000-row sample to parquet (UNTESTED; shuffled so rows are not state-ordered):
```
python -c "from datasets import load_dataset as L; import pandas as pd; d=L('nvidia/Nemotron-Personas-USA',split='train',streaming=True).shuffle(seed=42,buffer_size=100000).take(20000); pd.DataFrame(list(d)).to_parquet('.data/personas_20k.parquet')"
```
  Alternative: `huggingface-cli download nvidia/Nemotron-Personas-USA --repo-type dataset --local-dir .data/nemotron`, then `pandas.read_parquet(...).sample(20000, random_state=42)`. The full dataset is large (about 0.94B tokens); check size first.
- Caveats: synthetic, census-aligned only on the joint demographics NVIDIA modelled (occupation independent of ZIP given age/sex/education); no medical content. Avoid Nemotron-Personas-USA-Extended (NVIDIA Dataset License on NGC).

### 1.3 Last-resort fallback: census-marginal synthetic personas
Sample with a fixed seed (numpy) from public US marginals: age x sex (ACS B01001), race/ethnicity (B03002), household income bands (B19001), education (B15003), urbanicity (RUCA), caregiving (published prevalence), risk tolerance and health literacy from stated priors. Draw independently, or by iterative proportional fitting on age x sex x education. All inputs are US federal public data, no license risk. Cost about half a day; weaker joint structure than Nemotron. For NSCLC, reweight by age (median diagnosis about 70) and sex using SEER/ACS incidence, as the architecture already plans.

## 2. MiroFish-Offline (https://github.com/nikmcfly/MiroFish-Offline)
- License: AGPL-3.0 (GitHub API `spdx_id`). Fork of MiroFish; about 2.6k stars; last push 2026-03-24. Run only as a separate unmodified service over HTTP; never copy code into this repo.
- Stack (docker-compose.yml): `mirofish` (built from source; ports 3000 UI, 5001 API), `neo4j:5.18-community` (2 GB heap), `ollama/ollama` (reserves an NVIDIA GPU via `deploy.resources.reservations`; remove that block on a no-GPU host).
- Run: clone; `cp .env.example .env`; `docker compose up -d`; `docker exec mirofish-ollama ollama pull qwen2.5:32b` (or `qwen2.5:14b`) and `ollama pull nomic-embed-text`; open http://localhost:3000. Docker was not running on this host, so none of this was executed.
- LLM providers: any OpenAI-compatible endpoint via `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`; CAMEL/OASIS also read `OPENAI_API_BASE_URL` / `OPENAI_API_KEY`. Gemini's OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`) should work for chat by setting those variables (inferred from the config shape; UNVERIFIED). Embeddings use a separate `EMBEDDING_BASE_URL` pointing at Ollama's native API, so Ollama with `nomic-embed-text` is still needed (small, fine on CPU) unless a shim is written. Calls from this separate service bypass `clinova/common/llm.py` and `MAX_GEMINI_CALLS`, so give it its own cap or key, or use local Ollama.
- Inputs: source documents (press releases, policy drafts, reports) uploaded in the UI. Outputs: Neo4j knowledge graph, generated agent personas, simulated social posts/replies with sentiment and topic tracking, a ReportAgent report, per-agent chat. Steps to a report: upload document, build graph, generate environment, run simulation, generate report. The :5001 API routes are not documented on the README; T16 must read `backend/` to find them (read, do not copy).
- Hardware stated: minimum 16 GB RAM, 10 GB VRAM (14b model); recommended 32 GB RAM, 24 GB VRAM, 50 GB disk. CPU-only works but is slow.
- Setup-time estimate: 1.5 to 3 hours to a first report on a GPU laptop with working Docker (build, about 20 GB model pulls, debugging); 1 to 2 hours if Gemini serves chat and Ollama only does embeddings; half a day, or cut it, with no GPU or Docker trouble. It is optional; keep the cut order in ARCHITECTURE.md.
- Verification status: MiroFish is partly verified (license, compose, env, outputs from the repo); nothing was executed.

## 3. License table (docs/ARCHITECTURE.md dependencies and data)
| Name | License | Obligations | Risk to MIT repo |
| --- | --- | --- | --- |
| ClinicalTrials.gov API v2 / data | US government, public domain; NLM terms ask for attribution | Cite source and retrieval date | Low |
| Nemotron-Personas-USA | CC BY 4.0 | Attribution, indicate changes | Low. Chosen persona source |
| MatrAIx Persona 1M | Research-only, non-commercial (custom); upstream CC BY-SA, ODbL, NORC, CC BY-NC mixes | No commercial use, no relicensing | **High. Do not use or ship** |
| MatrAIx code | MIT | notice | None (unused) |
| MiroFish-Offline | AGPL-3.0 | Source of modified versions must be offered | Medium only if copied or modified; none if run unmodified as a separate service |
| Neo4j Community (MiroFish dependency) | GPL-3.0 | Only if redistributed; we do not ship it | Low |
| Ollama / Qwen2.5 | MIT / Apache-2.0 for 32b (3B and 72B differ) | notice | Low |
| Gemini API / `google-genai` SDK | SDK Apache-2.0; API under Google Gemini API Terms | No competing-model training on outputs; keep key private; no real patient data | Low; add a disclosure line for cached outputs in seed data |
| Tiger Cloud / TimescaleDB | Core Apache-2.0; community features under Timescale License (TSL); `timescaledb-ha` image mixes licenses | TSL forbids offering it as a hosted DBaaS | Low. App backend use only |
| PostgreSQL, pgvector | PostgreSQL License | notice | None |
| LightGBM, SHAP | MIT | notice | None |
| scikit-learn | BSD-3-Clause | notice | None |
| FastAPI | MIT | notice | None |
| Streamlit | Apache-2.0 | notice | None |
| Docker Engine / Compose | Apache-2.0 (Docker Desktop has separate commercial terms) | none | Low |
| pytest, ruff, uv | MIT (uv: MIT or Apache-2.0) | notice | None |
| GitHub Actions | GitHub terms | none | None |
| ntfy | Apache-2.0 / GPL-2.0 dual; we only POST to it | none | None |
| US Census, BLS, SEER, CDC published statistics | US federal, public domain (use published aggregates only) | cite each number's source | Low |
| Published literature distributions (stage, EGFR rates) | Facts are not copyrightable; do not copy tables or figures verbatim | cite | Low |
| Clinical Trials Arena article | Copyright | cite only | Low |

Not re-verified live (from general knowledge, check before release): Docker Desktop terms, ntfy license split, Qwen2.5 size-specific licenses, TimescaleDB TSL scope.
