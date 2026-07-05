# Contributing

<p align="center">
  <a href="#build-instructions"><img src="https://img.shields.io/badge/%F0%9F%9B%A0%EF%B8%8F-Build_Instructions-f7a800" alt="Build Instructions"></a>
  <a href="#workflow"><img src="https://img.shields.io/badge/%F0%9F%93%90-Workflow-7dd3a8" alt="Workflow"></a>
  <a href="#how-it-works"><img src="https://img.shields.io/badge/%E2%9A%99%EF%B8%8F-How_It_Works-7ab3ff" alt="How It Works"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/%F0%9F%93%9C-CC_BY--NC--ND_4.0-d4d4d4" alt="License"></a>
</p>

## Build Instructions

```powershell
git clone https://github.com/Yosef-Bunick/AUDIT-TEST.git
cd AUDIT-TEST
pip install -e .
```

Self-audit (runs tests + all checks):

```powershell
cd AUDIT-TEST
audit-test v
```

## Workflow

1. Fork or branch from `main`
2. Make changes, keep commits small and focused
3. Every change must pass all module checks. Allowed statuses:
   - `[PASS]` — clean (required for all modules)
   - `[WARN]` — allowed only on `quality`, and only for Q5 (coverage) or Q6 (docstring coverage). All other quality checks must be clean.
   - `[FAIL]` — **not allowed on any module** (means HIGH findings exist)
   - `[CRASH]` — **not allowed** (audit itself broke)

   Required commands before every PR: 
   ```
   cd AUDIT-TEST               # cd to your clone
                               # use current version to test your new version
   audit-test fix              # format + lint-fix
   audit-test --skip quality    # must pass all (no WARN allowed)
   audit-test q v              # quality verbose — only Q5/Q6 may WARN
   ```
4. If a finding is intentional and necessary, suppress it with `# audit: ok` on the exact line and explain why in the commit message
5. By submitting a PR, you agree that your contribution is licensed under CC BY-NC-ND 4.0
6. Open a PR

## How It Works

`audit-test` runs 6 checks against your code:

| Module | What it does |
|---|---|
| `wiring` | Dead symbols, config drift, disconnected code |
| `phd` | Exception discipline, security patterns, state bugs |
| `runtime` | Unbounded loops, missing timeouts, encoding traps |
| `suite` | Runs pytest, classifies real vs pollution failures |
| `quality` | Black, ruff, mypy, CVE scan, coverage |
| `gate` | Judges working-tree diff vs HEAD — fail-closed |

Bare words or flags — both work:

```powershell
audit-test phd high           # PHD only, HIGH severity
audit-test fix                # auto-format
audit-test gate               # judge your changes
```

## License

CC BY-NC-ND 4.0 — see [LICENSE](LICENSE).
