# TODO

Planned work that isn't scheduled for a release yet. Low priority unless marked.

## `auto backup` and `auto restore`

Save a developer's local state before a cluster is recreated, and put it back
afterwards. `auto stop --delete-cluster` keeps deleting everything; these are
the opt-in way to keep data. Recreating a cluster isn't a frequent need for the
team, so this is low priority.

- `auto backup [name]` writes one backup directory, e.g.
  `~/.auto/backups/<name or timestamp>/`, holding:
  - MySQL: `mysqldump --all-databases --single-transaction --routines --events`
  - Postgres: `pg_dumpall`
  - floci-aws: every S3 bucket's objects
  - Redis: optional, since it mostly holds caches and sessions
  - a small manifest: auto version, k3s version, pods and databases included,
    and when it was taken
- `auto restore [name]` loads a backup into a running cluster: databases
  first, then buckets. By default it refuses to overwrite a database that
  already has tables, unless forced.
- Open questions:
  - Back up only system pods that are running, or fail if one is missing?
  - Can pods be selected (`auto backup kirkland`), or only everything?
  - Should `auto stop --delete-cluster` offer to run `auto backup` first?
- The manual commands this replaces are in `docs/releases/0.9.0.md`
  ("Upgrading").

## Follow-ups from 0.9 (Envoy Gateway)

- Once everyone is on 0.9: drop the Ingress from each pod repo's main
  manifest, leaving only `.auto/routes.yaml` (the 0.9.0 release notes and the
  route template promise this for a later release).
- The production ingress move (forward auth, session affinity, cert-manager,
  production overlays) is its own project. Its starting points are kept
  outside this repo.

## Open-source launch follow-ups

- Give portal a landing page of its own instead of nginx's welcome page, so
  the first thing a new user sees says it came from auto.
- Write `docs/releases/X.Y.Z.md` for every release: the release workflow uses
  it as the GitHub release's description.
