# Release Process

Step-by-step guide for releasing a new version of **langclaw**.

## Overview

| Tool        | Purpose                          |
|-------------|----------------------------------|
| `uv`        | Package manager & lock file      |
| `hatchling` | Build backend                    |
| `pre-commit`| Code quality (ruff, yaml, toml)  |
| GitHub Actions | CI (lint + test) and publish  |
| PyPI        | Package registry (trusted publisher) |

## Versioning

The project follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

The **single source of truth** for the version is `pyproject.toml` → `[project].version`.

When releasing, also keep the runtime/exported version in sync:

- `langclaw/__init__.py` → `__version__` should match `[project].version`

## Important Constraint

The `no-commit-to-branch` pre-commit hook **blocks all direct commits to `main`**. Every change — including version bumps — must go through a feature branch and PR.

## Release Trigger (Mandatory)

Merging a PR to `main` triggers `.github/workflows/auto-tag-release.yml`.
That workflow reads `pyproject.toml` version and automatically creates/pushes a matching `v*` tag (for example `v0.2.0`) if it does not already exist.
Publishing starts from that tag via `.github/workflows/publish.yml`.

## Step-by-Step

### 1. Develop on a feature branch

```bash
git checkout -b feat/my-feature main
# ... make changes, commit ...
```

### 2. Bump the version (on the feature branch)

Before opening the PR, bump the version as the final commit on the branch.

Edit `pyproject.toml`:

```diff
-version = "0.1.3"
+version = "0.2.0"
```

Sync the lock file:

```bash
uv sync
```

Commit the bump:

```bash
git add pyproject.toml uv.lock
git commit -m "chore: bump version to 0.2.0"
```

### 3. Open a Pull Request

```bash
git push -u origin feat/my-feature
gh pr create --title "feat: my feature" --body "Description of changes"
```

CI runs automatically on every PR:
- **Lint** — `pre-commit run --all-files` (ruff format + lint, yaml/toml checks, trailing whitespace, large file guard)
- **Test** — `pytest tests/ -v` on Python 3.11, 3.12, 3.13
- **Check** — `alls-green` gates merge; all jobs must pass

### 4. Merge to `main`

Once CI is green and the PR is approved, merge to `main` via GitHub.
After merge, GitHub Actions automatically creates and pushes `vX.Y.Z` from the version in `pyproject.toml`.

### 5. Optional verification

```bash
git tag -l "v*"
git ls-remote --tags origin
```

### 6. Automated publish (GitHub Actions)

When the auto-tag workflow pushes a `v*` tag, it triggers `.github/workflows/publish.yml`:

1. **Build** — `python -m build` produces wheel + sdist
2. **Publish** — uploads to PyPI via trusted publisher (OIDC, no API token needed)
3. **GitHub Release** — `gh release create` with auto-generated release notes

No manual intervention needed after merge to `main` (assuming workflows are healthy).

### 7.1. Deployment / Consuming the new version

This repository is a framework/library, so “deployment” typically lives in the consuming app.
After the tag push publishes `langclaw==X.Y.Z` to PyPI, your deployment should:

- bump the dependency in your app (for example `langclaw==X.Y.Z`)
- update any Docker/Helm/K8s build steps in your app to install the same pinned version

If your deployment is container-based, make sure your Docker build uses the pinned version, e.g.:
`pip install "langclaw==X.Y.Z"`

### 7. Verify

- Check the [GitHub Actions](https://github.com/tisu19021997/langclaw/actions) run
- Confirm the package is live: `pip install langclaw==0.2.0`
- Review the auto-generated GitHub Release and edit notes if needed

## Quick Reference

```bash
# On the feature branch, after all changes are done:
# edit pyproject.toml version
uv sync
git add pyproject.toml uv.lock
git commit -m "chore: bump version to X.Y.Z"
git push -u origin feat/my-feature

# Open PR, wait for CI, merge via GitHub

# After merge:
# auto-tag creates and pushes vX.Y.Z from pyproject.toml version
# publish workflow runs from that tag
# Done — CI handles build, publish, and GitHub Release
```

## Manual Fallback (if auto-tag fails)

If the auto-tag workflow fails, create and push the tag manually:

```bash
git checkout main
git pull origin main
git tag -a vX.Y.Z -m "Release vX.Y.Z - description"
git push origin vX.Y.Z
```

## Conventions

| Area              | Convention                                         |
|-------------------|----------------------------------------------------|
| Branch naming     | `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`   |
| Commit messages   | Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`) |
| Tag format        | `vMAJOR.MINOR.PATCH` (e.g. `v0.2.0`)              |
| Bump commit       | `chore: bump version to X.Y.Z`                    |
| Pre-commit        | Runs automatically on commit; CI also runs it      |
| Direct to `main`  | **Not allowed** — `no-commit-to-branch` hook enforced |
