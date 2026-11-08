---
hide:
  - navigation
---

# auto

**One command from `git clone` to your whole platform running on a local
Kubernetes cluster.**

You list your services' git repositories in one config file. `auto start` does
the rest:

1. Creates a k3s cluster in Docker with a local image registry.
2. Clones each repo and builds its image.
3. Starts the databases each service asks for.
4. Deploys each service with its own Helm chart or Kubernetes YAML.
5. Serves it over HTTPS on a `.local` hostname.

```bash
curl -fsSL https://www.devocho.com/auto.sh | bash
export PATH="$PATH:$HOME/.auto"   # new terminals get this from your shell's rc file
auto start
```

The [install page](getting-started/install.md) lists what auto needs and the
`/etc/hosts` lines to add first.

<div class="grid cards" markdown>

-   **A new developer on day one**

    ---

    Install auto, load the project's config with `auto install`, run
    `auto start`. Every service, database and certificate is set up in
    minutes, not over a week of setup notes.

-   **Your real manifests**

    ---

    Each service deploys with the same Helm chart or YAML you ship. If
    production runs on Kubernetes, local development exercises the same
    objects.

-   **Databases and email included**

    ---

    MySQL, Postgres, Redis, MinIO and a Mailpit inbox ship with auto. A
    service names what it needs and auto starts it and creates its databases
    and buckets.

-   **HTTPS on every host**

    ---

    A locally trusted certificate covers every hostname your routes declare,
    including multi-label ones like `portal.app.local`, and updates as you add
    routes.

-   **Edit code live**

    ---

    Your code folder is mounted into the cluster, so a pod can run your
    working copy and pick up changes without rebuilding its image.

-   **Easy to rebuild**

    ---

    If something breaks, `auto stop --delete-cluster` and `auto start` bring
    back a clean environment in a couple of minutes.

</div>

## Where to start

- **New to auto:** [Install](getting-started/install.md), then
  [your first cluster](getting-started/first-cluster.md).
- **Putting your own service in auto:** [Add your app](guides/add-your-app.md),
  then [expose it with a route](guides/routes.md).
- **Upgrading from 0.8:** read the [0.9.0 release notes](releases/0.9.0.md)
  first. Every cluster has to be recreated once.
- **Something is wrong:** [Troubleshooting](troubleshooting.md).

auto is open source under the MIT License. [DevOcho](https://www.devocho.com)
builds it and uses it every day on its own client projects. Issues and pull
requests are welcome on [GitHub](https://github.com/DevOcho/auto).
