# Spike T03: NSCLC clinical trait distributions

Population: US adults with advanced or metastatic NSCLC who are candidates for systemic therapy. Machine-readable version: [`assumptions.draft.yaml`](assumptions.draft.yaml) (the integrator turns it into `clinova/cohort/assumptions.yaml`).

## How to read this

- **Evidence labels.** `primary` = number read from the paper's own text or tables. `excerpt` = seen only in a search excerpt or abstract; re-verify before quoting. `assumption` = no published number for this exact quantity; value proposed with reasoning. 16 of 42 flagged YAML entries are assumptions.
- **Honesty rule.** Nothing here was tuned on any outcome data. Every proposed assumption is listed under "Assumptions to revisit" at the end.
- **Main anchor cohorts** (all US): Flatiron EHR advanced NSCLC treated with PD-(L)1 inhibitors, 2015-2017, n=5,256 ([Khozin/Miksad, Cancer 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6899461)); Flatiron 1L immunotherapy 2016-2020, n=7,312 ([Waterhouse data-in-brief](https://pmc.ncbi.nlm.nih.gov/articles/PMC8209237)); LCMC1, stage IV adenocarcinoma, n=733 ([Kris, JAMA 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4163053)); BIDMC genotyping, n=381 ([Costa, Lung Cancer 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3800098)); AACR GENIE v8 by race ([Frontiers Oncol 2022](https://frontiersin.org/journals/oncology/articles/10.3389/fonc.2022.946625/full)).
- **Known bias.** PD-(L)1-treated Flatiron cohorts under-represent never-smokers and driver-positive patients (they receive TKIs). Academic genotyping cohorts over-represent them. The YAML notes which direction each number leans.

## Demographics

| Trait | Proposed distribution | Evidence | Source |
| --- | --- | --- | --- |
| Age | <50: 3.0%, 50-64: 30.1%, 65-74: 36.7%, >=75: 30.2% (median 69, IQR 61-75) | primary | [Cancer 2019 Table 2](https://pmc.ncbi.nlm.nih.gov/articles/PMC6899461), [Flatiron](https://resources.flatiron.com/publications/characteristics-of-real-world-metastatic-non-small-cell-lung-cancer-patients-treated-with-nivolumab-and-pembrolizumab-during-the-year-following-approval). All-stage SEER median age 71 ([SEER](https://seer.cancer.gov/statfacts/html/lungb.html)) |
| Sex | Male 53.6%, female 46.4% | primary | same Flatiron cohort; SEER age-adjusted incidence 52.0 vs 43.6 per 100k |
| Race | White 77.3%, Black 9.3%, Asian 2.7%, other 10.7% (of 4,766 with race recorded) | primary | same cohort; academic BIDMC cohort has 13.1% Asian |
| Hispanic ethnicity | 6% (independent overlap flag) | assumption | SEER incidence about half of non-Hispanic White; reasoning in YAML |
| Smoking | Never 15%, former/current 85% | assumption | anchors 7-11% (PD-(L)1 cohorts) to 28-34% (BIDMC, LCMC1) |

## Disease

| Trait | Proposed distribution | Evidence | Source |
| --- | --- | --- | --- |
| Stage at initial diagnosis (patients now advanced) | 0-I 7.6%, II 6.6%, III 23.8%, IV 62.0% | primary | [Cancer 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6899461). SEER all-stage: distant 51%, regional 21%, localized 24% |
| Current stage at screening | IV 90%, IIIB/C 10% | assumption | no source found |
| Histology | Nonsquamous 70%, squamous 30% | primary | 69.6% nonsquamous (3,510/5,045); 65-72% in three other cohorts |
| ECOG 0 / 1 / 2 / 3+ | 20% / 45% / 27% / 8% (ECOG>=2 = 35%) | assumption | ECOG>=2 reported 34-48% of aNSCLC ([ESMO Open consensus](https://pmc.ncbi.nlm.nih.gov/articles/PMC8689080)); Flatiron ALK+ cohort 61% ECOG 0-1, 13.3% ECOG 2-4, 26% undocumented ([JTO CRR 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11327465)); BIDMC pembrolizumab 8.1/52.7/33.8/5.4% ([JAMA Netw Open 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC7879233/)); Seville cohort 20.5/43.2/28.4/8.0% ([Biology 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8472602)) |

ECOG by age (assumption, logistic with OR 1.5 per decade for ECOG>=2, marginal fixed at 35%): ECOG 0/1/2/3+ is 0.256/0.575/0.130/0.039 under 50, 0.231/0.520/0.192/0.057 at 50-64, 0.197/0.443/0.278/0.082 at 65-74, 0.167/0.376/0.353/0.104 at 75+. Direction supported by BIDMC (median age 65 for ECOG 0-1 vs 72 for ECOG>=2); magnitude is not published.

## Biomarkers

Draw one driver category per patient: drivers are almost always mutually exclusive (BIDMC), 3% had 2+ in LCMC1.

| Marker | Proposed (nonsquamous, never / ever smoker) | Squamous | Evidence |
| --- | --- | --- | --- |
| EGFR sensitizing | 46% / 5% | 3.4% (assumption for smoking split) | primary (BIDMC n=179 triple-tested); squamous from [Oncol Rep 2011](https://doi.org/10.3892/or.2011.1182) |
| ALK | 14% / 2% | 0.5% / 0.2% (assumption) | primary (BIDMC) |
| ROS1 | 3.5% / 1.0% | 0.2% / 0.1% (assumption) | assumption: US unselected 1.7% (18/1,073, [Bergethon JCO 2012](https://pmc.ncbi.nlm.nih.gov/articles/PMC3295572)); never-smoker 3.4% from a [Korean series](https://ir.ymlib.yonsei.ac.kr/handle/22282913/87472) |

Cross-checks: LCMC1 reports EGFR sensitizing 17%, ALK 8%, KRAS 25% in stage IV adenocarcinoma (34% never-smokers); the table gives 19% EGFR in that mix. With 15% never-smokers the model yields about 11% EGFR, 3.8% ALK and 1.7% ROS1 among nonsquamous patients.

Ancestry and smoking variation:

| Group | EGFR | ALK | ROS1 | Source |
| --- | --- | --- | --- | --- |
| GENIE Asian | 61.75% | 5.08% | 3.56% | [Frontiers 2022](https://frontiersin.org/journals/oncology/articles/10.3389/fonc.2022.946625/full) |
| GENIE White | 22.56% | 3.15% | 1.32% | same |
| GENIE Black | 33.67% | 2.81% | 2.5% | same |
| BIDMC Asian / White / Black | 62% / 18.4% / 18.2% | 14.8% / 7.8% / 0% (n=27/192/21) | n/a | [Costa 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3800098) |
| Never-smoker Asian women (Korea, n=198) | 63% | 7% | 1% | [Lung Cancer series](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4467161/), excerpt only |
| Hispanic/Latino (Latin America meta-analysis) | 22% | 5% | 2% | [Javeriana](https://perfilesycapacidades.javeriana.edu.co/en/publications/prevalence-of-oncogenic-driver-mutations-in-hispanicslatin-patien/), excerpt only |

Model rule: multiply EGFR odds by 5.5 for Asian patients (GENIE OR), cap at 0.65. GENIE "EGFR" includes non-sensitizing alterations and has no smoking data, so this is approximate. Black and Hispanic adjustments are low confidence (small or non-US data).

**PD-L1 TPS (driver-negative, tested):** <1% 33%, 1-49% 37%, >=50% 30% (assumption, medium confidence). Published: 31/42/27 ([multinational chart review](https://www.analysisgroup.com/Insights/publishing/real-world-outcomes-of-first-line-treatment-with-anti-pdl1-based-combination-therapy-for-nonsquamous-metastatic-non-small-cell-lung-cancer-a-multiregional-chart-review-in-europe-japan-and-the-united-states/), excerpt); 48/30/22 ([China, J Cancer 2021](https://jcancer.org/v12p7390), excerpt); 38.7/37.0/24.3 computed from Flatiron 1L chemo-IO patients with a result ([data-in-brief](https://pmc.ncbi.nlm.nih.gov/articles/PMC8209237), biased low at >=50% because those patients often get monotherapy); high PD-L1 about 27-30% per [OncLive](https://onclive.com/view/frontline-pembrolizumab-practicechanging-for-advanced-nsclc). About 22% of nonsquamous Flatiron patients have no PD-L1 result.

## Brain metastases and prior therapy

| Trait | Proposed | Evidence | Source |
| --- | --- | --- | --- |
| Any brain metastases on record | Nonsquamous 14%, squamous 5% | primary | Flatiron 1L IO: 468/3,457 and 317/2,166 nonsquamous; 42/814 and 42/875 squamous ([data-in-brief](https://pmc.ncbi.nlm.nih.gov/articles/PMC8209237)) |
| Higher estimates | 26% of stage IV at presentation; 10.4% across all stages | primary | [NCDB, Waqar 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC6990432), which also gives adenocarcinoma 12.99% vs squamous 4.89% (OR 2.93) and OR 0.70 per 10 years of age |
| Treated-and-stable vs untreated (among those with brain mets) | 60% / 40% | assumption | no source found; run sensitivity at 80/20 and 40/60 |
| Reaches 2nd / 3rd line (of all treated advanced NSCLC) | 51.6% / 26.4% (n=8,811) | primary | [ConcertAI/UMN 2025](https://www.concertai.com/publications/real-world-treatment-patterns-among-advanced-non-small-cell-lung-cancer-patients-in-the-us) |
| Prior lines for an unrestricted-line screening pool | 0: 56.2%, 1: 29.0%, 2+: 14.8% | assumption (derived) | derived from the line shares above; condition on the trial's required line when it has one |
| Prior EGFR TKI among PD-(L)1 starters | 5.4%; no prior therapy 25.3% | primary | [Cancer 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6899461) |
| Prior perioperative or chemoradiation systemic therapy | 25% | assumption | 38% were stage 0-III at diagnosis |

## Organ function and labs

**Creatinine clearance.** Compute with Cockcroft-Gault ([Nephron 1976](https://doi.org/10.1159/000180580)): CrCl = (140 - age) x weight / (72 x SCr) x 0.85 if female. Inputs: weight lognormal (sigma_log 0.24) around NHANES means by sex and age ([NCHS Series 3 No. 46](https://www.cdc.gov/nchs/data/series/sr_03/sr03-046-508.pdf), e.g. females 60-69 78.2 kg, 80+ 67.9 kg; males 60-69 91.2 kg, 80+ 80.5 kg); SCr lognormal, median 0.95 male / 0.75 female, sigma_log 0.25 (assumption, no NSCLC creatinine distribution found). One-off simulation with the age bands above: 21% below 60 mL/min, 7% below 45, 56.5% below 90, median 84. Comparators: pan-cancer Cockcroft-Gault in 1,217 patients (Turkey) had 6.8% in 30-59 and 0.6% in 15-29 ([Adv Ther 2005](https://link.springer.com/article/10.1007/BF02850082), younger mixed population, excerpt); Flatiron PD-(L)1 cohort labels 2.4% moderate and 0.5% severe renal failure, definition unclear. General-population weight overstates NSCLC patients' weight; a `weight_multiplier` knob (default 1.0) is in the YAML.

**Other labs: no NSCLC baseline distribution was found for the usual thresholds.** All failure probabilities are assumptions (first line / later line): Hb<9 5% / 10%, platelets <100 2% / 5%, ANC <1.5 3% / 6%, AST or ALT >2.5x ULN 4% / 5%, bilirubin >1.5x ULN 2% / 3%. Anchors: pan-cancer anemia (Hb<10 in 10% at enrollment, Hb<12 in 39%; [ECAS summary](https://doi.org/10.2147/CMAR.S45236), excerpt); Flatiron hepatic-failure label 1.3% ([Cancer 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6899461)); a lymphocyte >500/uL criterion excludes 6.3% of Flatiron aNSCLC patients on average ([Trial Pathfinder](https://doi.org/10.1038/s41586-021-03430-5)). Thresholds themselves come from protocols such as [NCT02574078](https://cdn.clinicaltrials.gov/large-docs/78/NCT02574078/Prot_000.pdf) and [NCT03739710](https://cdn.clinicaltrials.gov/large-docs/10/NCT03739710/Prot_SAP_001.pdf). Trial Pathfinder also found that relaxing bilirubin, platelet, hemoglobin and ALP cut-offs within the range trials use did not change hazard ratios, so these labs matter less for outcomes than for eligibility counts.

## Key correlations

ECOG rises with age (BIDMC; assumption on size). EGFR is higher in never-smokers (46% vs 5%) and Asian patients (OR about 5.5 in GENIE, 7.2 in BIDMC). ALK and ROS1 patients are younger and mostly never-smokers (Flatiron ALK+ median age 63, 53% never-smokers). KRAS (not a Clinova trait) is 43% in smokers vs 6% in never-smokers, which keeps driver-negative patients from being double-counted. CrCl falls with age through the formula and falling weight after 70. Brain metastases are more common in nonsquamous disease (OR 2.93) and in younger patients (OR 0.70 per decade). Lab abnormalities should correlate with ECOG>=2 and liver metastases (qualitative; not quantified).

## Share of real patients who meet typical trial eligibility

| Estimate | Value | Evidence | Source |
| --- | --- | --- | --- |
| 10 phase III aNSCLC trials emulated in Flatiron | Mean 30% of treated patients met all encodable criteria (1,553 of 5,167 on average); data-driven relaxation gives 3,209 (about 62%) | primary | [Liu et al., Nature 2021](https://doi.org/10.1038/s41586-021-03430-5) (61,094 patients); its introduction says about 80% of aNSCLC patients did not meet criteria |
| Single academic center, first-line metastatic, n=163 | 64.4% eligible (chemo 72.1%, immunotherapy 40.0%, targeted 58.8%) | excerpt | [CancerNetwork](https://www.cancernetwork.com/view/clinical-trial-eligibility-and-outcomes-in-patients-with-metastatic-nsclc-treated-outside-of-clinical-trials) |
| 786-patient review | 60% ineligible; brain metastases 41% and ECOG 3-4 25% lead | excerpt | [Clin Lung Cancer 2018](https://www.sciencedirect.com/science/article/abs/pii/S1525730418301347) |
| Earlier cited range | 27-45% eligible | excerpt | [Curr Oncol](https://doi.org/10.3747/co.25.3978) |
| German CRISP registry, n=7,774 | Among ineligible: ECOG>=2 60.9%, brain metastases 49.3% | excerpt | [JTO CRR 2023](https://www.jtocrr.org/article/S2666-3643(23)00169-8/fulltext) |

Sanity band for the simulator's screen output on a typical phase 3 protocol: central 30%, plausible 25-65%. Output outside 15-80% is a bug signal. This is a plausibility check, not a calibration target.

## Assumptions to revisit

Hispanic share (6%); never-smoker share (15%); current stage IV share (90%); ECOG marginal (35% >=2) and its age gradient (OR 1.5/decade); squamous ALK/ROS1/EGFR-by-smoking cells; ROS1 never/ever split; PD-L1 band split; treated vs untreated brain metastases (60/40); prior perioperative therapy (25%); serum creatinine distribution; all five lab failure probabilities and their later-line multipliers; the Asian EGFR cap (0.65); Hispanic ALK multiplier (2.0). Best value for future time: a Flatiron or SEER-Medicare pull of ECOG by age and baseline labs, which would replace most of the low-confidence rows.

## Gaps and caveats

Full texts for several excerpt-only items (CRISP, 786-patient review, Cur Oncol, Asian never-smoker series, PD-L1 chart reviews, ECAS, Turkish renal study) were not accessible; their numbers should be re-verified against the papers before external use. No SEER stage IV-only histology or ECOG table exists in the sources retrieved, so histology and ECOG rely on Flatiron and small cohorts.
