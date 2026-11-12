# Commands

Every command takes `-h` or `--help`. Where a command takes `<pod>`, use the
short name, the name of the pod's repo (`portal`, not
`portal-596d876cff-pc99c`); auto looks up the rest. With
[tab completion](../getting-started/install.md#turn-on-tab-completion-optional)
on, pod names complete too.

## The cluster

### `auto start [pod]`

With no pod: start everything. Creates the cluster if there isn't one,
resumes it if it is stopped, then pulls, builds and deploys every pod in
`local.yaml` with the system pods they need. See
[what `auto start` does](../getting-started/how-it-works.md#what-auto-start-does).

With a pod: start that one pod in the running cluster. Pulls its repo, builds
and pushes its image, starts the system pods it needs, creates its databases
and buckets, deploys it and applies its routes, then refreshes the
certificate if it brought new hostnames.

| Option | |
| --- | --- |
| `--dry-run` | Print what would happen without changing anything. |
| `--offline` | Skip git pulls and registry downloads, for working without a network. |

### `auto stop [pod]`

With no pod: stop the cluster. Everything in it is kept for the next
`auto start`.

With a pod: remove that pod (`helm uninstall` or `kubectl delete`, matching
how it was installed) and its routes, and leave the rest running.

| Option | |
| --- | --- |
| `--delete-cluster` | Delete the cluster and its databases instead of stopping it. The registry, your code and your config are kept. |
| `--dry-run` | Print what would happen without changing anything. |

### `auto status`

A table of the cluster, the registry and the pods.

| Option | |
| --- | --- |
| `--namespace`, `-n` | Show pods in this namespace (default: `default`). |
| `--all-namespaces`, `-a` | Show pods in every namespace. |
| `--watch`, `-w` | Refresh every 3 seconds; `q` or ctrl+c to exit. |

### `auto images`

List the images running in the cluster, formatted for the
`registry:` section of `local.yaml`. auto already adds new ones after each
`auto start`, so this is mostly for seeing what is cached.

## Pods

### `auto restart <pod>`

Stop the pod and start it again, re-reading its `.auto/config.yaml`, its
manifests or chart, and its routes. Use it after changing any of them.

### `auto refresh <pod>`

Delete the pod's running instances so Kubernetes recreates them from the same
Deployment. Much quicker than `restart`; use it to pick up a newly pushed
image or to clear a stuck process. Works on system pods too
(`auto refresh mysql`).

### `auto tag <pod>`

Build the pod's image from its `Dockerfile`, tag it with the pod's
`version`, and push it to the local registry.

| Option | |
| --- | --- |
| `--refresh`, `-r` | Refresh the pod afterwards so it runs the new image. |

### `auto upgrade <pod>`

Rebuild the pod's image and push it to the local registry, as `auto tag`
does.

### `auto logs <pod>`

Follow the pod's logs, with Kubernetes health-check requests filtered out. If
the pod isn't running, it says what state it is in instead.

## Databases

### `auto seed <pod>`

Reset the pod's database with its `init-command`, then load test data with
its `seed-command`. Both run in a short-lived copy of the pod's Deployment, so
they work even when the pod itself won't start. See
[seed data](../guides/add-your-app.md#6-seed-data).

Any extra arguments after the pod name are passed to the `seed-command` as-is,
e.g. `auto seed my-pod --admin-setup`.

### `auto init <pod>`

Run only the pod's `init-command`: the database as it is before seed data and
migrations.

### `auto migrate <pod>`

Run the pod's database migrations with [smalls](https://github.com/DevOcho/smalls),
DevOcho's migration tool for Python and Peewee, in a short-lived copy of the
pod.

### `auto rollback <pod> <number>`

Roll the pod's database back to migration `<number>` with smalls:
`auto rollback shop 0123`.

### `auto mysql`

Open a MySQL shell on the `mysql` system pod.

### `auto postgres`

Open a `psql` shell on the `postgres` system pod.

### `auto mailpit`

Forward the Mailpit inbox to <http://127.0.0.1:8025/>, for when
`mailpit.local` isn't in `/etc/hosts`. ctrl+c closes it.

## Setup

### `auto install <git_repo>`

Clone a [project repo](../guides/teams.md) into the code folder and install
its `local.yaml` as yours. Your current one is saved as
`~/.auto/config/local.yaml.bak`.

### `auto update [version]`

Update auto to the latest release, or move to `version` (up or down):
`auto update 0.8.3`. A version that doesn't exist leaves your install as it
is.

| Option | |
| --- | --- |
| `--dry-run` | Show what would happen without changing anything. |
| `--force` | Reinstall even when already on the latest release (ignored with a version). |

### `auto autocomplete`

Print the line that turns on tab completion for your shell.

| Option | |
| --- | --- |
| `--shell` | `bash` (default), `zsh` or `fish`. |
| `--install` | Append the line to your shell's rc file, after asking. |

## Environment variables

| Variable | |
| --- | --- |
| `AUTO_NO_UPDATE_CHECK=1` | Don't check GitHub for a newer release. |
| `AUTO_VERSION` | For the installer: install this release instead of the latest. |
