<div align="center">

# auto

**One command from `git clone` to your whole platform running on a local
Kubernetes cluster.**

[![CI](https://github.com/DevOcho/auto/actions/workflows/ci.yml/badge.svg)](https://github.com/DevOcho/auto/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/DevOcho/auto)](https://github.com/DevOcho/auto/releases)
[![Docs](https://img.shields.io/badge/docs-devocho.github.io%2Fauto-c80036)](https://devocho.github.io/auto/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[Documentation](https://devocho.github.io/auto/) ·
[Try it](#try-it-in-five-minutes) ·
[Why auto?](#why-auto) ·
[Release notes](https://github.com/DevOcho/auto/releases)

</div>

![A first `auto start` on a new machine: it builds portal's image, creates the cluster and serves https://portal.local/ in about three minutes, shown here sped up](docs/assets/demo.gif)

Your product is a handful of services, two databases, a storage bucket and an
email provider. Getting a new developer running takes a day of setup notes,
and every laptop ends up a little different.

auto puts that setup in config files that live in your repos. You list your
services' git repositories in one file, and `auto start`:

- creates a [k3s](https://k3s.io) cluster in Docker, with a local image registry,
- clones each repo and builds its image,
- starts the MySQL, Postgres, Redis and Mailpit instances your services ask
  for, plus S3 and other AWS services through [Floci](https://floci.io), and
  creates their databases and buckets,
- deploys each service with its own Helm chart or Kubernetes YAML,
- serves each one over trusted HTTPS on a `.local` hostname, through
  [Envoy Gateway](https://gateway.envoyproxy.io/).

A fresh cluster is up in about two minutes, and a stopped one resumes in
about 35 seconds. When something breaks, deleting the cluster and starting
again is a normal, fast thing to do.

## Try it in five minutes

You need Docker, [k3d](https://k3d.io), kubectl and git, plus
[mkcert](https://github.com/FiloSottile/mkcert) and certutil for HTTPS
(certutil is in `libnss3-tools` on Debian and Ubuntu). auto runs on Linux,
macOS (Apple Silicon) and Windows through WSL2.

```bash
curl -fsSL https://www.devocho.com/auto.sh | bash
export PATH="$PATH:$HOME/.auto"   # new terminals get this from your shell's rc file
echo "127.0.0.1 k3d-registry.local portal.local" | sudo tee -a /etc/hosts
auto start
```

Then open <https://portal.local>. The default config runs
[portal](https://github.com/DevOcho/portal), a small example service. Next,
[add your own](https://devocho.github.io/auto/guides/add-your-app/).

## How it works

One file on your machine lists the services:

```yaml
# ~/.auto/config/local.yaml
pods:
  - repo: git@github.com:your-org/api.git
  - repo: git@github.com:your-org/web.git
```

Each service's repo says how to run it, so its own team keeps it current:

```yaml
# api/.auto/config.yaml
name: api
version: 1.4.0
command: helm install          # or: kubectl apply -f .auto/deployment.yaml
seed-command: scripts/seed.py  # run by `auto seed api`
system-pods:
  - name: postgres
    databases:
      - name: api
  - name: mailpit
```

Your code folder is mounted into the cluster, so a pod can run your working
copy with a reloading dev server: save a file and the change is live, with no
image rebuild. A team keeps the shared `local.yaml` in a project repo, and a
new developer's first day is `auto install <project repo>` and `auto start`.

## Why auto?

| If you use | It gives you | What auto adds |
| --- | --- | --- |
| Docker Compose | Containers on your machine | Kubernetes. Your Helm charts and manifests run locally, not for the first time in staging. |
| kind, k3d or minikube | An empty cluster | Everything after that: the registry, HTTPS, databases, cloning and building every repo. auto uses k3d underneath. |
| Tilt, Skaffold or DevSpace | A rebuild-and-redeploy loop for the code you're editing | The environment around it: the cluster, shared databases and many repos from one config. They work together; point Tilt at auto's cluster. |

auto is opinionated: one local cluster per machine, `.local` hostnames, and
the system pods it ships. If you don't deploy to Kubernetes, Docker Compose is
simpler. If your team develops against a shared remote cluster, auto isn't
for that either.

## Documentation

Everything is at **[devocho.github.io/auto](https://devocho.github.io/auto/)**:

- [Install](https://devocho.github.io/auto/getting-started/install/) and
  [your first cluster](https://devocho.github.io/auto/getting-started/first-cluster/)
- [Add your app](https://devocho.github.io/auto/guides/add-your-app/) and
  [expose it with a route](https://devocho.github.io/auto/guides/routes/)
- [Databases and system pods](https://devocho.github.io/auto/guides/system-pods/),
  [Mailpit](https://devocho.github.io/auto/guides/mailpit/),
  [HTTPS](https://devocho.github.io/auto/guides/https/)
- [Share a setup with your team](https://devocho.github.io/auto/guides/teams/)
- [Moving from ingress-nginx](https://devocho.github.io/auto/guides/from-ingress-nginx/)
- Reference: [commands](https://devocho.github.io/auto/reference/commands/),
  [`local.yaml`](https://devocho.github.io/auto/reference/local-yaml/),
  [`.auto/config.yaml`](https://devocho.github.io/auto/reference/pod-config/)
- [Troubleshooting](https://devocho.github.io/auto/troubleshooting/)

## Upgrading from 0.8

0.9 replaces ingress-nginx, which was retired in March 2026, with Envoy
Gateway, and needs a newer Kubernetes. Every existing cluster has to be
recreated once, which resets its local databases. Back them up first; the
[0.9.0 release notes](https://devocho.github.io/auto/releases/0.9.0/) have the
commands, and the way back to 0.8.3.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the development setup and how changes are tested.

## License

[MIT](LICENSE). Made with love by [DevOcho](https://www.devocho.com), who use
it every day to build their clients' platforms.
