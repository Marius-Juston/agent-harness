# Stratum N selection log (PROTOCOL.md amendments 1 and 1a, walked 2026-10-13)

Population: [scripts/n_population.sql](../scripts/n_population.sql) (80 packages) then
[scripts/n_population_ext.sql](../scripts/n_population_ext.sql) (117 packages, first 14 walked), in
`cityHash64(concat(name, '196'))` order. Reasons: (a) the first Python usage example needs credentials, a server, a
GPU, a model download, another service, or an artefact built by the package's own CLI, or the package has no Python
usage example (CLI or app only); (b) README not in English; (c) a plugin or extension that needs a host application.
No package failed (d) (G1 or G2 of `dev reuse check`).

## Main population (rule 9)
| # | package | decision |
|---|---|---|
| 1 | pylint-gajaguar | skip (c) pylint plugin, no Python example |
| 2 | multi-workspace | skip (a) CLI only |
| 3 | remnawave-api | skip (a) API server |
| 4 | doorcore | skip (b) README in Chinese |
| 5 | ppgrid | skip (a) the first Python block post-processes output files with rasterio, not the package API |
| 6 | acedatacloud-x402 | skip (a) payment API |
| 7 | multiplayer | **accept** |
| 8 | ovos-hivemind-pipeline-plugin | skip (c) |
| 9 | ovos-skill-cosquin-tales | skip (c) |
| 10 | reminis | **accept** |
| 11 | 1claw-crewai-tools | skip (a) API key |
| 12 | speckit-specops | skip (a) CLI only |
| 13 | qlam-core | skip (a) cloud service |
| 14 | overleaf-comments-export | skip (a) Overleaf service |
| 15 | corent | skip (a) API key |
| 16 | ovos-skill-arxiv-papers | skip (c) |
| 17 | saucier | skip (a) CLI only |
| 18 | ace | skip (a) no Python usage example |
| 19 | hlinor-registry | skip (a) needs a bundle compiled by its own CLI |
| 20 | ovos-skill-cmd | skip (c) |
| 21 | releasing | skip (a) CLI only |
| 22 | modelflowib | skip (a) no Python usage example |
| 23 | political-comms | skip (a) API |
| 24 | cortexdb-connectors | skip (a) service |
| 25 | openstockapi | skip (a) network data |
| 26 | outrage | skip (a) database server and API key |
| 27 | oversampleqa | skip (a) no Python usage example |
| 28 | cangling-message | skip (a) broker server |
| 29 | ovos-control-panel | skip (c) |
| 30 | librarian-a2a | skip (a) LLM API |
| 31 | remotedesktop | skip (a) GUI app for Windows |
| 32 | bitcoin-core-rpc | skip (a) node server |
| 33 | powerrules | skip (a) CLI only |
| 34 | doc-lattice | skip (a) CLI only |
| 35 | acdd | skip (a) no Python usage example |
| 36 | ovos-skill-andrew-lang-tales | skip (c) |
| 37 | compalf3896lg | skip (a) network device |
| 38 | odoo-addon-l10n-br-sped-base | skip (c) |
| 39 | qiskit-gym-mcp-server | skip (a) server |
| 40 | mlx-optiq | skip (a) Apple Silicon and model download |
| 41 | outlabs-taskq | skip (a) Postgres |
| 42 | actions-http-helper | skip (a) network download |
| 43 | capa-language | **accept** |
| 44 | fractal-core | **accept** |
| 45 | correxit | skip (c) JupyterLab extension |
| 46 | ovos-a2a-solver-plugin | skip (c) |
| 47 | ovos-common-query-pipeline-plugin | skip (c) |
| 48 | icelake | skip (a) LLM API |
| 49 | fiscalrail | skip (a) API key |
| 50 | dbt-altertable | skip (c) dbt adapter |
| 51 | ezgb | **accept** |
| 52 | camt053-lsp | **accept** |
| 53 | comodor | skip (a) no Python usage example |
| 54 | reporemedy | skip (a) no Python usage example |
| 55 | fragment-ton-api | skip (a) wallet and service |
| 56 | comparative-edge | skip (a) no Python usage example |
| 57 | compileml | skip (a) example needs xgboost and a reason dictionary not in the package |
| 58 | memloom | skip (a) no Python usage example |
| 59 | simple-module-pagebuilder | skip (c) |
| 60 | reporting-labs | skip (c) pytest plugin |
| 61 | ovos-chromadb-embeddings-plugin | skip (c) |
| 62 | remedy-ai | skip (a) no Python usage example |
| 63 | reliquary | skip (a) no Python usage example |
| 64 | python-yama | skip (a) no Python usage example |
| 65 | ovos-m2v-pipeline | skip (c) |
| 66 | dbrownell-placeholder | skip (a) no Python usage example |
| 67 | relia-sdk | skip (a) no Python usage example |
| 68 | multi-forge | skip (a) no Python usage example |
| 69 | multi-ocr-py | skip (b) and (a) API key |
| 70 | remote-jobs-api | skip (a) no Python usage example |
| 71 | qiskit-pasqal-provider | skip (a) no Python usage example |
| 72 | megavers | skip (a) cloud storage |
| 73 | compare-schema-data | skip (a) databases |
| 74 | actions-core | skip (a) the example's point is to run under the Action Server |
| 75 | correctover-runtime-guard | skip (a) no Python usage example |
| 76 | repopy | skip (a) CLI only |
| 77 | corral-herdr | skip (a) CLI only |
| 78 | 3thfloor | skip (a) model file |
| 79 | carrotquant-data | skip (b) and (a) network data |
| 80 | ovos-gguf-plugin | skip (c) |

Accepted from the main population: 7 (multiplayer, reminis, capa-language, fractal-core, ezgb, camt053-lsp, and
none after 52). actions-core was first noted as a candidate and then skipped under (a) on reading its README, before
any case for it was written.

## Extension (amendment 1a)
| # | package | decision |
|---|---|---|
| 1 | simlar | skip (a) model download |
| 2 | dot-agora | **accept** |
| 3 | fish-speech-lib | skip (a) GPU and model |
| 4 | doqa-pytest | skip (c) pytest plugin |
| 5 | 1xai-sdk | skip (a) API key |
| 6 | cas-schedule-p | **accept** |
| 7 | dnorio-agent-meter | skip (a) API key |
| 8 | faskill | **accept** |
| 9 | poly-basis-ml | **accept** |
| 10 | satusehat-integration | **accept** |
| 11 | saurix | skip (a) CLI only |
| 12 | ovos-media-plugin-mass | skip (c) |
| 13 | fetchnode | skip (c) Django app and monitoring service |
| 14 | comparer-mcp | **accept** (the README's engine example has no MCP dependency) |

12 accepted. **Deviation found after acceptance:** the ClickHouse mirror's first upload is not PyPI's. By the
upload times in the PyPI JSON API, 4 of the 12 were first released before 2026-07-15: poly-basis-ml (2026-02-02),
ezgb (2026-04-01), multiplayer (2026-05-03) and camt053-lsp (2026-06-17). They stay in N under rule 9 as written; the
note reports the 8 post-cutoff packages (16 cases) separately.
