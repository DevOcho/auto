# Contributing

auto is open source under the MIT License, and contributions are welcome:
bug reports, documentation fixes, new system pods and code.

- **Found a bug or want a feature?**
  [Open an issue](https://github.com/DevOcho/auto/issues/new/choose). For a
  bug, include `auto --version`, your OS and the output of the command that
  failed.
- **Spotted a mistake on this site?** Use the edit button at the top of the
  page. Each page is a Markdown file in the repo's `docs/` folder.
- **Want to change the code?** Read
  [CONTRIBUTING.md](https://github.com/DevOcho/auto/blob/main/CONTRIBUTING.md)
  for the layout, the development setup, the tests and how pull requests are
  reviewed.

## Preview the site locally

```bash
git clone git@github.com:DevOcho/auto.git && cd auto
pip install -r docs-requirements.txt
zensical serve        # http://localhost:8000, rebuilds as you edit
```

The site is built with [Zensical](https://zensical.org) from
`zensical.toml` and published to GitHub Pages on every push to `main`.
