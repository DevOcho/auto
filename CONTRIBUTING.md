# Contributing to auto

Thanks for helping. Bug reports, documentation fixes, new system pods and code
are all welcome.

Everyone taking part follows the short [code of conduct](CODE_OF_CONDUCT.md).

## Reporting a bug or asking for a feature

[Open an issue](https://github.com/DevOcho/auto/issues/new/choose). For a bug,
include `auto --version`, your OS, and the output of the command that failed.
`auto status` and `kubectl get pods -A` often help too.

## Fixing the docs

Every page on the [documentation site](https://devocho.github.io/auto/) has an
edit button that opens the file on GitHub. The pages are Markdown in `docs/`.
To preview the site locally:

```bash
pip install -r docs-requirements.txt
zensical serve        # http://localhost:8000, rebuilds as you edit
```

The README is the project's front page. Keep it short and send readers to
the site for details.

## Working on the code

auto is a Python CLI (Click and Rich) that runs `k3d`, `kubectl`, `helm`,
`docker` and `git`. The layout:

```text
auto/
├── auto.py            # entry point
├── autocli/
│   ├── commands.py    # every Click command
│   ├── core.py        # cluster bootstrap, pod install and removal
│   ├── config.py      # loads ~/.auto/config/local.yaml into CONFIG
│   ├── services.py    # system pods, databases, buckets
│   ├── registry.py    # the local registry: builds, pushes, caching
│   ├── https.py       # Envoy Gateway, certificates, routes
│   ├── checks.py      # dependency checks
│   ├── runner.py      # one-shot pods for seed, init and migrate
│   ├── utils.py       # subprocess, git and kubectl helpers
│   └── test_*.py      # tests, next to the code they test
├── config/local.yaml  # the default config the installer ships
└── k3s/               # manifests for system pods and Envoy Gateway
```

Set up a development environment:

```bash
git clone git@github.com:DevOcho/auto.git
cd auto
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r build_requirements.txt
pre-commit install
```

Run auto from your checkout:

```bash
python auto/auto.py --help
```

It reads the manifests from `~/.auto/k3s/`, not from the repo. To try a
change to a file under `auto/k3s/`, copy it there first.

### Tests and checks

```bash
pytest auto/autocli          # the test suite
pre-commit run --all-files   # black, isort, flake8, pylint, codespell, the tests
```

CI runs the tests on Ubuntu, Debian, Fedora, Arch and macOS. Most of auto
drives a real cluster, so for anything that changes what happens to one,
also try it: `auto stop --delete-cluster`, `auto start`, and the commands you
touched. Say what you ran in the pull request.

### Style

- User-facing output goes through `rprint` from Rich, never `print()`.
- Fatal errors use `utils.declare_error()`, so every error looks the same.
- Shell commands go through `utils.run_and_wait()` and its siblings.
- Comments explain why, especially about infrastructure behavior that isn't
  obvious from the code. Keep existing ones when you change the code around
  them.
- Lines up to 110 characters (black and flake8 enforce it).

### Commits and pull requests

- Keep each pull request to one change, with a test where one fits.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `fix(registry): ...`, `feat(https): ...`, `docs: ...`.
- A change users will notice gets a line in the next release's notes in
  `docs/releases/`.

## Releases

Maintainers release by pushing a `vX.Y.Z` tag. GitHub Actions builds the
Linux and macOS binaries and publishes the release, with
`docs/releases/X.Y.Z.md` as its notes. The version is `VERSION` in
`auto/autocli/commands.py`.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
