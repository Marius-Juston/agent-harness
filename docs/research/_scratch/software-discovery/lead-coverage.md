# Lead measurement: PyPI and GitHub coverage (2026-10-13)

Script: [scripts/pypi_github_coverage.py](scripts/pypi_github_coverage.py) (random seed 13; PyPI simple index JSON,
packages.ecosyste.ms package and lookup endpoints, `gh api search/repositories`). VERIFIED, small samples.

- PyPI simple index: 912,487 projects; packages.ecosyste.ms counts 941,619 PyPI packages.
- 150 random PyPI projects: repository URL on github.com 93 (62%), none 45 (30%), lookup failed 9 (6%), gitlab.com 2,
  codeberg.org 1. So about a third of PyPI is invisible to a GitHub-only search.
- Top 150 PyPI projects by downloads: github.com 146 (97%), none 4. Popular packages are almost all on GitHub.
- GitHub Python repositories pushed since 2025-10-01, 50 per star band: with a PyPI package 11/50 (100-300 stars),
  18/50 (300-1k), 23/50 (1k-5k), 28/50 (>5k). Most GitHub Python repositories are applications or research code,
  not installable libraries.

Reading: neither index contains the other. GitHub search finds the popular libraries plus non-packaged prior art;
PyPI adds the long tail with no repository link, but PyPI has no search API, so it needs its own (offline) index.
