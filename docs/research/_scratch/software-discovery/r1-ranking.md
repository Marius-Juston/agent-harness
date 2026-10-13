# r1: ranking, judging and build-vs-reuse for `dev reuse`

Search coverage: openalex, crossref, europepmc and arxiv answered; s2 down (HTTP 429, shared pool); arXiv MCP, WebSearch and WebFetch used for full text and docs; 2026-10-13.

Scripts (git-ignored, in this checkout): `.scratch/r1/scorecard_agg.py` and `.scratch/r1/quality_score.py`. Both run with `python3 -I`, use only keyless APIs, and were run on 2026-10-13.

## Findings

### F1. Proposed formula: gate first, rank on relevance, use quality as a bounded bonus

```
candidate passes the gates G1..G6 (below)          -> otherwise FAIL (drop) or HOLD (show with a warning)
S   = R + λ·Q                                      R = semantic relevance in [0,1], from the retrieval round
λ   = 0.15                                         ASSUMED: equals the pilot's largest stars bonus (0.03·log10(1+1e5) = 0.150, VERIFIED)
Q   = Σ_k w_k · f_k                                each f_k is in [0,1]
f   = log(1+S)/log(1+max(S,T))                     the OpenSSF criticality_score normalisation, per raw signal S with cap T
w   = maturity .242, documentation .199, maintenance .172, security .152, popularity .117, community .117
```

- **Weights.** Each weight is the share of the 115 surveyed developers who rated that factor "high influence", normalised over the six factors that can be measured automatically. The raw shares are 62, 51, 44, 39, 30 and 30%, from [Larios Vargas et al. 2020, Table 2](https://arxiv.org/abs/2005.12574) (SOURCED). The arithmetic was re-checked by `quality_score.py`, which prints the weights (VERIFIED). Usability (55% high) and fit for purpose (36%) cannot be measured from metadata. Fit for purpose is R itself; usability is left to the agent or the owner at shortlist time (BELIEVED).
- **Normalisation.** The form is the one the [ossf/criticality_score README formula](https://github.com/ossf/criticality_score) uses (SOURCED, read from its formula image): C = (1/Σα)·Σ α_i·log(1+S_i)/log(1+max(S_i,T_i)). It is bounded, saturates at the cap and makes heavy-tailed counts comparable.
- **Signals per factor** (thresholds T are ASSUMED and should be tuned on the #169 benchmark):
  - maturity: months since first release (T=60) and number of releases (T=50). SourceRank also rewards a 1.0+ release and a project older than 6 months ([libraries.io `source_rank.rb`](https://github.com/librariesio/libraries.io/blob/main/app/models/concerns/source_rank.rb), SOURCED).
  - documentation: a docs URL in the project metadata, otherwise README length (T=10 kB). README size is one of three significant predictors of highly-selected npm packages ([Mujahid et al. 2022](https://arxiv.org/abs/2204.04562), SOURCED).
  - maintenance: the larger of the Scorecard Maintained score divided by 10 and release recency (1 at ≤180 days, falling linearly to 0 at 730 days). The 180 days is SourceRank's "recent release"; the 730 is ASSUMED. See F3 for why Maintained cannot stand alone.
  - security: the Scorecard aggregate divided by 10, or 0.5 when unknown. It is kept low because the outcome evidence is mixed (F3).
  - popularity: monthly downloads (T=1e6, pypistats) and stars (T=1e4) with equal weight. Stars are capped because they can be faked (F4).
  - community: contributor count (T=50), or forks as a proxy when only deps.dev is queried. The CHAOSS Contributor Absence Factor is the better signal where GitHub is available (F3).
- **Prototype (VERIFIED, `quality_score.py`, 2026-10-13).**
  - requests: Q = 0.973.
  - httpx: Q = 0.75. Its Scorecard Maintained is 0 (F3).
  - nose: Q = 0.415. Its last upload was 4,150 days ago and its licence string is "non-standard".
  - `reqeusts`, `0requests` and a made-up name all fail G1.
  - The scores order the packages sensibly. They are not validated against any outcome.

### F2. Gates, including the package-hallucination guard

| gate | rule | evidence |
|---|---|---|
| G1 exists | PyPI `pypi.org/pypi/<name>/json` returns 200, otherwise FAIL | Commercial models hallucinate at least 5.2% of packages and open models 21.7%, over 576,000 samples from 16 LLMs ([Spracklen et al. 2024](https://arxiv.org/abs/2406.10279), SOURCED). On 500 re-prompted cases, 43% of hallucinations repeated in all 10 runs and 58% more than once ([same, §5.3](https://arxiv.org/abs/2406.10279), SOURCED), so they can be squatted. The name "slopsquatting" is credited to Seth Larson ([Infosecurity Magazine, 2025-04-14](https://www.infosecurity-magazine.com/news/ai-hallucinations-slopsquatting/), SOURCED, secondary). A nonexistent name returns 404 (VERIFIED). |
| G2 not malicious | OSV `POST api.osv.dev/v1/query` returns no `MAL-` id, otherwise FAIL | [ossf/malicious-packages](https://github.com/ossf/malicious-packages) feeds OSV (Apache-2.0, pushed 2026-10-13, VERIFIED). `0requests` returns MAL-2026-15859 and `0-8` returns MAL-2025-2925. Both are already gone from PyPI with a 404 (VERIFIED), so G1 catches removed ones. G1 alone is not enough because a squatted name exists. |
| G3 not brand new | first release at least 90 days ago, otherwise HOLD | One working definition counts a package registered after the model's cutoff, or less than 90 days before the model's release, as a hallucination ([Krishna et al. 2025, §2.2](https://arxiv.org/abs/2501.19012), SOURCED). [Scorecard Maintained](https://github.com/ossf/scorecard/blob/main/docs/checks.md) also only assesses projects older than 90 days (SOURCED). |
| G4 alive | not archived, not deprecated (deps.dev `isDeprecated`), not all yanked. More than 730 days since the last upload means "dormant": HOLD | SourceRank subtracts 5 points each for deprecated, unmaintained and removed ([source](https://github.com/librariesio/libraries.io/blob/main/app/models/concerns/source_rank.rb), SOURCED). Of 1,932 popular GitHub projects, 16% were abandoned and 41% of those survived ([Avelino et al. 2019](https://arxiv.org/abs/1906.08058), SOURCED). In npm, packages are removed significantly faster when their end-of-life status is stated explicitly ([Miller et al., ICSE 2025](https://doi.org/10.1109/icse55347.2025.00004), SOURCED, abstract only). nose's classifier still says "Production/Stable" and deps.dev does not mark it deprecated, so only idle time catches it (VERIFIED). The 730-day threshold is ASSUMED. |
| G5 licence | Blue Oak permissive tiers (Model, Gold, Silver, Bronze) pass. Weak copyleft (LGPL, MPL, EPL) passes as an unmodified dependency, with a flag. Strong or network copyleft (GPL, AGPL, SSPL, EUPL) FAILs unless the owner overrides. Unknown or non-standard: HOLD | Licence is "high influence" for 45% of developers ([Larios Vargas 2020](https://arxiv.org/abs/2005.12574), SOURCED). Of the libraries ChatGPT recommended, 14.2% had copyleft licences it did not mention ([Latendresse et al. 2024](https://arxiv.org/abs/2408.05128), SOURCED). In PyPI, 7.27% of releases have a licence incompatibility and 61.3% of those come from transitive dependencies ([Xu et al., ASE 2023](https://arxiv.org/abs/2308.05942), SOURCED), so the transitive closure needs a check too (deps.dev `:dependencies`). In the [Blue Oak lists](https://blueoakcouncil.org/list), MIT, Apache-2.0, BSD-2-Clause and ISC are Silver; BSD-3-Clause, PSF-2.0, 0BSD and Unlicense are Bronze; MPL-2.0 and LGPL fall in the "weak" copyleft family (VERIFIED from `list.json` and `copyleft.json`, v16 and v11). The tier mapping is a policy (BELIEVED). |
| G6 known vulns | open OSV or GHSA advisories on the latest version: flag, not FAIL | Across 10 LLMs, 36.7 to 55.7% of tasks with a version pinned by the model contain at least one known CVE ([PinTrace, Wang et al. 2026](https://arxiv.org/abs/2605.06279), SOURCED). So the model never picks the version; uv resolves it. |

- **Typo distance alone is a weak guard.** Only 13.4% of hallucinated names are within Levenshtein distance 1 to 2 of a real package, and 8.7% of hallucinated Python names are real npm packages ([Spracklen 2024, §5.4](https://arxiv.org/abs/2406.10279), SOURCED). The existence check (G1) plus OSV (G2) plus the ecosystem check matter more than edit distance (BELIEVED).
- **Upstream mitigations, cited only for completeness, not for the harness.** RAG over the top-20k PyPI names cut hallucination from 16.14 to 12.24% (DeepSeek) and from 26.28 to 13.40% (CodeLlama); fine-tuning cut it to 2.66 and 10.27% ([Spracklen 2024, Table 4](https://arxiv.org/abs/2406.10279), SOURCED). In the ASE 2026 evaluation, RAG reduced the package hallucination rate in 18 of 32 model-language configurations ([Djiré et al.](https://doi.org/10.1145/3832783.3837555), SOURCED, abstract). That paper also found earlier methods overstated Python rates by 9.4 points by counting stdlib modules, so the guard must whitelist `sys.stdlib_module_names` (BELIEVED). BOUND, a model edit, cut the package-level hallucination rate by 79.9% on edit prompts ([Liu et al. 2026](https://arxiv.org/abs/2607.02052), SOURCED). None of these is usable by a harness that cannot retrain the model, which is why the guard is a post-hoc registry check.
- **Testing the agent's own model.** garak (NVIDIA, Apache-2.0, 9,546 stars, pushed 2026-10-10) ships `probes/packagehallucination.py` and a matching detector (VERIFIED via the GitHub API). Krishna et al. ran their study with it ([§3](https://arxiv.org/abs/2501.19012), SOURCED).

### F3. Maintenance and security signals: what holds up

- **The Scorecard aggregate is a risk-weighted mean.** The weights are Critical 10, High 7.5, Medium 5 and Low 2.5, over checks scored 0 to 10, per the [Scorecard README](https://github.com/ossf/scorecard) (SOURCED). Recomputed from `api.scorecard.dev` for psf/requests: API 8.3, recomputed 8.29 with checks scoring -1 left out (VERIFIED, `scorecard_agg.py`). Both `api.scorecard.dev` and `api.deps.dev/v3` answer with no key; deps.dev embeds the scorecard, licence, stars, `isDeprecated`, advisories and the source-repo link (VERIFIED).
- **Maintained counts only the last 90 days.** It wants at least one commit a week in the previous 90 days ([checks.md](https://github.com/ossf/scorecard/blob/main/docs/checks.md), SOURCED). encode/httpx (15,640 stars, last default-branch commit 2026-02-23) scores 0: "0 commit(s) and 0 issue activity found in the last 90 days" (VERIFIED, deps.dev, scorecard dated 2026-08-24). That is why F1 takes the larger of Maintained and release recency. On the 3,220 repositories behind the top 1% of PyPI libraries by PageRank, the bucketed Maintained score can be forecast with accuracy above 0.95 ([Tsakpinis et al. 2026](https://arxiv.org/abs/2601.18344), SOURCED). A trend term is therefore possible later.
- **The security score's outcome evidence is mixed.**
  - In npm and PyPI models, Scorecard practices explained only 9 to 12% of the variance (R²) in vulnerability counts. A higher aggregate went with *more* reported vulnerabilities ([Zahan et al., ICSE-SEIP 2023](https://arxiv.org/abs/2210.14884), SOURCED). The most important practices were Maintained, Code-Review, Branch-Protection and Security-Policy.
  - Across 145K npm packages, aggregate adoption was associated with 5.2 fewer vulnerabilities and a mean time to remediate 216.8 days faster ([Zahan et al. 2025](https://arxiv.org/abs/2504.14026), SOURCED).
  - Conclusion: keep security as a mid-weight factor, never a gate (BELIEVED).
- **Bus factor.** The CHAOSS "Contributor Absence Factor" (formerly Bus Factor) is the smallest number of contributors who together make 50% of the contributions ([CHAOSS](https://chaoss.community/kb/metric-bus-factor/), SOURCED). Of 133 popular GitHub projects, 65% have a truck factor of 2 or less ([Avelino et al. 2016](https://arxiv.org/abs/1604.06766), SOURCED). It needs per-author commit counts from the GitHub API, which costs one call per candidate, so compute it for the top 5 only (BELIEVED).

### F4. Popularity: useful, biased and fakeable

- **What predicts selection.** Downloads, stars and README size predict highly-selected npm packages (2,527 packages, regression; survey of 118 developers) ([Mujahid 2022](https://arxiv.org/abs/2204.04562), SOURCED). Three in four of 791 surveyed developers look at stars before use ([Borges & Valente 2018](https://arxiv.org/abs/1811.07643), SOURCED).
- **Stars can be faked.** StarScout found about six million suspected fake stars, surging in 2024, mostly on short-lived malware and AI/LLM repositories ([He et al., ICSE 2026](https://arxiv.org/abs/2412.13459), SOURCED). Hence the cap (T=1e4), the half weight next to downloads, and the 0.117 total weight.
- **Popularity bias.** Library recommenders over-recommend popular items, and the literature has barely addressed it ([Nguyen et al., MSR 2023](https://doi.org/10.1109/msr59073.2023.00016), SOURCED). LLMs, even fine-tuned or with penalties, did not fix it ([Di Sipio et al. 2025](https://arxiv.org/abs/2501.10313), SOURCED). This is the argument for R dominating S and λ staying small (BELIEVED).

### F5. Existing free scorers: none is validated

| tool | form | status (VERIFIED, GitHub API, 2026-10-13) |
|---|---|---|
| libraries.io SourceRank | Integer sum: +1 each for basic info, repo, README, licence, more than one version, semver, a release in the last 6 months, older than 6 months, and 1.0+. Log10 scores for dependents (×2), dependent repos, stars, contributors/2 and subscribers/2. -2 if all releases are prereleases, -1 for outdated deps, -5 each for deprecated, unmaintained and removed ([source](https://github.com/librariesio/libraries.io/blob/main/app/models/concerns/source_rank.rb), SOURCED) | AGPL-3.0, 1,159 stars, pushed 2026-10-02. `libraries.io/api/pypi/requests/sourcerank` answered 200 without a key on 2026-10-13 (VERIFIED). Its rate limits are unknown. |
| best-of-generator `projectrank` | A similar sum of points and log terms; the README says "chosen by experience. There is no scientific proof" ([README](https://github.com/best-of-lists/best-of-generator), SOURCED) | GPL-3.0, 112 stars, pushed 2025-08-31 |
| OSSF criticality_score | Pike's weighted log formula with 10 signals (for example contributor_count weight 2 with T=5000) ([README](https://github.com/ossf/criticality_score), SOURCED). It measures criticality, not quality. | Apache-2.0, 1,461 stars. The README says the hosted runs stopped and the last successful run was July 2025. |
| OpenSSF Scorecard | Security-practice checks, risk-weighted | Apache-2.0, 5,750 stars, pushed 2026-10-12; keyless API |
| deps.dev API | Data source (scorecard, licence, advisories, deprecation, dependents) | google/deps.dev Apache-2.0, pushed 2026-10-13; keyless |
| guarddog | Heuristics for malicious PyPI and npm packages | Apache-2.0, 1,232 stars, PyPI 3.2.0 |
| pip-licenses | Licence inventory of an environment | MIT, PyPI 5.5.5 |
| chaoss/augur | CHAOSS metrics service | **archived** at that path. GrimoireLab is GPL-3.0 and heavy (BELIEVED). |

- **Best free option for `dev reuse`: deps.dev plus OSV plus PyPI JSON plus pypistats, all keyless.** It gives every F1 signal except contributor counts (from GitHub, top-k only) (VERIFIED, `quality_score.py`).
- Use SourceRank as a cross-check where libraries.io answers (BELIEVED).
- Nothing should be vendored. The AGPL and GPL licences of libraries.io, ecosyste.ms and best-of-generator rule out copying their code into an MIT harness, but calling their APIs is fine (BELIEVED, not legal advice).

### F6. LLM and RAG library recommendation, and code-to-library prior art

- **LLMs alone recommend poorly, and retrieval helps.**
  - Vanilla LLMs pick the right migration target first only 38.8% of the time on average (Precision@1; 61.5% within the top 10). LibRec's BM25 RAG over past migrations raises this to 50.5% (P@1) and 71.5% (P@10), averaged over 10 LLMs on LibEval: 2,888 Python migration records from 2,324 repositories ([Han et al. 2025](https://arxiv.org/abs/2508.09791), SOURCED). The models are from 2024 to 2025, such as GPT-4o-mini and Claude-3.7-Sonnet, so re-measure on current models.
  - LibEval's source-to-target pairs are a gold set the lead did not write, which the #169 DONE WHEN needs (BELIEVED).
  - ChatGPT (GPT-3.5) used third-party libraries about 10% more often than humans, and 6.5% of its libraries did not work out of the box ([Latendresse 2024](https://arxiv.org/abs/2408.05128), SOURCED).
- **Migration ranking.** He et al. combine four metrics (Rule, Message, Distance and API Support) into a multiplicative rank. On Java they reach MRR 0.8566 and top-10 NDCG 0.7702 ([SANER 2021](https://doi.org/10.1109/saner50967.2021.00016), SOURCED). The code is [MigrationHelper](https://github.com/hehao98/MigrationHelper) (GPL-3.0, last pushed 2024-04, VERIFIED). This is the "replace X with Y" mode, not discovery.
- **Code-to-library.**
  - Import2vec learns library vectors from import co-occurrence, in Java, JS and Python ([Theeten et al. 2019](https://arxiv.org/abs/1904.03990), SOURCED). That suits a `--from` mode: a file's imports become a query vector.
  - CrossRec recommends from a project's dependency set by collaborative filtering ([Nguyen et al., JSS 2019](https://doi.org/10.1016/j.jss.2019.110460), SOURCED, title only). Its repo, crossminer/CrossRec, is EPL-2.0 and was last pushed 2020 (VERIFIED, dormant).
  - Req2Lib maps requirement text to libraries, trained on 5,625 Java projects ([SANER 2020](https://doi.org/10.1109/saner48275.2020.9054865), SOURCED).
  - Reinvented-wheel detection: Kawrykow & Robillard detect client code that imitates an API method and found over 400 cases in 10 Java systems ([ASE 2009](https://doi.org/10.1109/ase.2009.62), SOURCED).
  - Inside a repository, coding agents leave duplicated logic in 50.8% of task chains by turn 5 ([RepoReuse, Ma et al. 2026](https://arxiv.org/abs/2609.35357), SOURCED). The cheapest "reuse" step is therefore a search of the agent's own repository (`uv run dev code find`) before any external search (BELIEVED).

### F7. Build-vs-reuse rule (proposed)

1. **Search inside first.** Run `dev code find` over the repository and check `sys.stdlib_module_names`. If either covers the need, reuse it (RepoReuse, above).
2. **If the need is trivial, build it.** Trivial means 35 lines or fewer and McCabe complexity of 10 or less. Trivial npm packages are 16.8% of the registry. Only 45.2% of them have tests, and 11.5% pull in more than 20 dependencies ([Abdalkareem et al., FSE 2017, as summarised by Never Work in Theory](https://neverworkintheory.org/2021/09/17/why-do-developers-use-trivial-packages.html), SOURCED, secondary). The 2020 npm+PyPI follow-up ([EMSE](https://doi.org/10.1007/s10664-019-09792-9)) is paywalled and was not read.
3. **Reuse when a candidate passes G1 to G6, R ≥ r_min and Q ≥ 0.5.** r_min is to be set from the benchmark (ASSUMED). Record the choice, licence and alternatives, as the project rules require.
4. **Ask the owner** when the best candidate is HOLD, strong copyleft, or Q < 0.5 with no alternative.
5. **Otherwise build,** and record the candidates that were rejected and why.

## Gaps not closed

- **No composite library score has been validated against an outcome** (adoption, abandonment, migration away). SourceRank, projectrank and criticality are all hand-weighted. The F1 weights are developer *perception* from one survey of 115 developers, so the #169 benchmark is the only validation. Suggest also checking Q against later abandonment, for example nose-like cases.
- **Data scientists weight factors differently.** Nadi & Sakr ([EMSE 2022](https://doi.org/10.1007/s10664-022-10241-3); summary via the [AUC repository record](https://fount.aucegypt.edu/faculty_journal_articles/4525), N=90 plus 18 in focus groups) find 11 factors rated differently, with security and licence lower, per a search-engine summary that was not verified. Their table is paywalled and was not read. The web-developer replication ([IJSEKE 2024](https://doi.org/10.1142/s0218194024500402)) was also not read past its abstract.
- Valiev et al. 2018 (PyPI sustained activity, [FSE](https://doi.org/10.1145/3236024.3236062)): only the first lines of the abstract were available. Their dormancy predictors could replace the ASSUMED 730-day threshold.
- The thresholds T, λ = 0.15 and the Q ≥ 0.5 cut-off are all ASSUMED. They should be swept on the 40-need benchmark.
- libraries.io answered without a key today, unlike the pilot's assumption. Its rate limits and terms were not checked.
- The guard covers PyPI only. npm, crates and other ecosystems have equivalent endpoints in deps.dev and OSV, but these were not tested.

## Sources fetched

- arXiv: 2005.12574, 2406.10279 (§5.3, §5.4, §6.2), 2501.19012 (§2.2, §4), 2607.02052, 2508.09791 (Table III), 1904.03990, 2204.04562, 1906.08058, 1604.06766, 2210.14884, 2504.14026, 2601.18344, 2412.13459, 1811.07643, 2308.05942, 2408.05128, 2605.06279, 2501.10313, 2609.35357.
- DOI abstracts (OpenAlex or Crossref): 10.1109/saner50967.2021.00016, 10.1145/3832783.3837555, 10.1109/msr59073.2023.00016, 10.1109/icse55347.2025.00004, 10.1109/saner48275.2020.9054865, 10.1109/ase.2009.62, 10.1145/3744916.3773157, 10.1109/msec.2023.3279773 (one line).
- Docs and code: ossf/scorecard README and docs/checks.md; ossf/criticality_score README, its formula image and `config/scorer/original_pike.yml`; libraries.io `source_rank.rb`; best-of-generator README; CHAOSS Contributor Absence Factor; Blue Oak `list.json` and `copyleft.json`; neverworkintheory.org trivial-packages summary; Infosecurity Magazine 2025-04-14; the fount.aucegypt.edu record.
- Live APIs (VERIFIED): api.scorecard.dev, api.deps.dev/v3, api.osv.dev, pypi.org JSON, pypistats.org, libraries.io sourcerank, and the GitHub REST API for repo metadata.
