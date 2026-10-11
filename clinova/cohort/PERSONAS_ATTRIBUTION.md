# Persona source and assumptions

The optional source is **NVIDIA, Nemotron-Personas-USA**,
<https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA>, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Its records are fully
synthetic; these are not real patients. The downloader records retrieval time,
source revision, license, sampling method, and SHA-256 in ignored data metadata.
Clinova selects source rows and importance-resamples their age × sex cells to
`assumptions.yaml` NSCLC margins. Resampling is with replacement: source UUIDs can
repeat, but generated persona IDs are unique. Download sampling uses randomly
selected non-overlapping 100-row blocks across the full split, rather than the
spike's shuffled streaming approach, to avoid adding `datasets` or downloading
multi-GB shards. Blocks introduce residual within-block dependence.

Age, sex, occupation, narrative, education and marital status are read directly
when present. Source geography is preserved in the narrative. Geography is **not**
a cancer-center distance observation: no ZIP-to-RUCA or trial-site lookup is
available in this implementation, so urbanicity and distance are sampled proxies.
No free text, ZIP, or name is used to infer race, ethnicity or clinical traits.

Race draws use the NSCLC reference YAML; `other_or_multiple` is mapped to `other`
because its published components cannot be identified. The independent Hispanic
probability overrides race to match the contract's Hispanic-of-any-race coding.
The contract cannot retain an overlapping race and ethnicity flag, so non-Hispanic
race shares equal the YAML probabilities multiplied by `(1 - Hispanic p)`.

## Explicit life-context priors (all assumptions, low confidence)

These are transparent illustrative priors, **not** published NSCLC measurements
or fitted outputs. Their `source` is empty; the links below describe the public
aggregate frameworks used to motivate the fallback and are not citations for the
specific numeric priors. Values are not tuned to eligibility or test outcomes.

| Parameter | Prior | source |
| --- | --- | --- |
| Urbanicity | urban 0.40, suburban 0.40, rural 0.20 | [] |
| One-way distance km | lognormal medians urban 15, suburban 35, rural 90; log SD 0.5 | [] |
| Travel access | easy <25 km, moderate 25–<75 km, hard ≥75 km | [] |
| Medical risk tolerance | low 0.35, medium 0.45, high 0.20 | [] |
| Education-conditioned literacy | bachelor/graduate: low 0.10, adequate 0.60, high 0.30; otherwise 0.35/0.60/0.05 | [] |
| Education-conditioned household income | bachelor/graduate: low 0.15, middle 0.45, high 0.40; otherwise 0.40/0.50/0.10 | [] |
| Caregiver | age <65: 0.25; age ≥65: 0.15; married adds 0.05 | [] |
| Synthetic occupation | service 0.30, office 0.30, manual 0.20, professional 0.20 | [] |
| Synthetic education | <high school 0.10, high school 0.28, some college 0.32, bachelor 0.30 | [] |
| Synthetic marital status | married 0.50, never married 0.20, widowed 0.15, divorced 0.15 | [] |
| Synthetic retirement | age ≥65: 0.80 | [] |

The fallback label `census_synthetic` follows the task/spike interface, but the
fallback is only census-inspired: social categories are approximate assumptions,
not a full ACS fit. Age/sex are sampled directly from the same NSCLC YAML target.
The public aggregate frameworks are [ACS demographic profiles](https://www.census.gov/programs-surveys/acs/data.html),
[Census urban/rural classification](https://www.census.gov/programs-surveys/geography/guidance/geo-areas/urban-rural.html),
and [NCES adult literacy](https://nces.ed.gov/naal/). A future revision can replace
these priors with retrieved ACS/RUCA/literacy tables without changing the API.
Absent/unlicensed/broken sources and sources missing any positive-mass age/sex
cell use this fallback. Source absence is logged at info level; invalid sources
or insufficient coverage are warnings.
