# Share a setup with your team

On a project with several services, each in its own repo, every developer
needs the same `local.yaml`: the same repos, branches and system pods. The
simplest place to keep it is a repository of its own.

## The project repo

A small repo for the project as a whole, next to the services' repos:

```text
your-project/
├── local.yaml     # the auto config every developer uses
├── README.md      # how the services fit together
└── docs/          # architecture, onboarding, runbooks
```

`local.yaml` lists every service repo:

```yaml title="local.yaml"
code: ${HOME}/source/your-project
https: true
pods:
  - repo: git@github.com:your-org/api.git
    branch: main
  - repo: git@github.com:your-org/web.git
    branch: main
  - repo: git@github.com:your-org/worker.git
    branch: main
system-pods:
  # copy the system-pods section from auto's default local.yaml
registry:
  # filled in by auto as it caches images; commit it so new clusters
  # start faster for everyone
```

## A new developer's first day

```bash
curl -fsSL https://www.devocho.com/auto.sh | bash
auto install git@github.com:your-org/your-project.git
auto start
```

`auto install` clones the project repo into the code folder, saves the
current `~/.auto/config/local.yaml` as `local.yaml.bak`, and copies the
project's `local.yaml` into place. `auto start` then clones every service,
builds it, starts its databases and serves it.

At DevOcho this takes a new developer from a fresh laptop to a running
platform in under ten minutes, even on the largest projects. The 2025 average
was two minutes.

## A shared dev setup

Some teams run one auto environment that several people use, on a machine
in the office or a VM. By default its ports listen only on that machine, so
open them in its `local.yaml`, on a network you trust:

```yaml title="~/.auto/config/local.yaml (on the shared machine)"
bind-address: 0.0.0.0
```

Then recreate its cluster and registry (see
[`bind-address`](../reference/local-yaml.md#bind-address)). On each machine
that uses it:

- Point the hostnames at the shared machine in `/etc/hosts`, for example
  `192.168.1.20 shop.local`.
- To trust its HTTPS certificate, install the shared machine's mkcert
  authority: copy `rootCA.pem` from `mkcert -CAROOT` there, and run
  `CAROOT=<folder with that file> mkcert -install`.

Anyone who can reach the shared machine can reach its databases with their
fixed passwords, and its registry and Kubernetes API, so keep it off networks
you don't control.

## Keeping it current

- When a service is added, add it to the project's `local.yaml`. Everyone
  else runs `auto install <project repo>` again, then `auto start`.
- Each service's own setup (its databases, seed scripts, routes) lives in
  that service's `.auto/` folder, so changing it needs no change to the
  project repo.
- Pin `branch:` per service when the team works on a long-running branch.
