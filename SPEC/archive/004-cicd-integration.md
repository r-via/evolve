# 004 — CI/CD Integration Examples

Archived from SPEC.md § CI/CD integration on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

## GitHub Actions

Evolve works in CI/CD pipelines out of the box. Here's a GitHub Actions
workflow that evolves a project and creates a PR with the results:

```yaml
name: Evolve
on:
  workflow_dispatch:
  schedule:
    - cron: '0 2 * * 1'  # Weekly on Monday at 2am

jobs:
  evolve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install evolve
        run: pip install .

      - name: Run evolution
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          evolve start . --check "pytest" --rounds 20 --max-cost 50 --json > evolve-output.jsonl
          echo "EXIT_CODE=$?" >> $GITHUB_ENV

      - name: Create PR on convergence
        if: env.EXIT_CODE == '0'
        uses: peter-evans/create-pull-request@v6
        with:
          title: 'feat: evolve convergence'
          body: |
            Automated evolution run converged.
            See `runs/*/evolution_report.md` for details.
          branch: evolve/ci-run
```

### Validation in CI

Use `--validate` as a quality gate in pull request checks:

```yaml
name: Spec Compliance
on: [pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install .
      - name: Validate spec compliance
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: evolve start . --validate --check "pytest"
```
