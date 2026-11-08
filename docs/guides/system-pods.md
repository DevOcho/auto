# Databases and system pods

auto ships ready-made manifests for the services your pods depend on. They
are called *system pods*. Each one is off until something asks for it.

## What ships

| System pod | What it is | From your pods | From your machine | Credentials |
| --- | --- | --- | --- | --- |
| `mysql` | MySQL 8.0 | `mysql:3306` | `localhost:3306`, or `auto mysql` for a shell | `root` / `password` |
| `postgres` | Postgres 18 | `postgres:5432` | `localhost:5432`, or `auto postgres` | `root` / `password`, database `postgres` |
| `redis` | Redis 7.2, append-only file on | `redis:6379` | `localhost:6379` | none |
| `minio` | S3-compatible object storage | `http://minio:9000` | `http://minio.local/` (API); console with `auto minio` | `minio` / `minio123` |
| `mailpit` | SMTP catch-all with a web inbox | `mailpit:1025` (SMTP) | `http://mailpit.local/`, or `auto mailpit` | none ([guide](mailpit.md)) |
| `floci-aws` | Floci AWS emulator (region `us-east-1`), in memory | `http://floci-aws:4566` | `http://floci-aws.local/` | |
| `floci-ui` | Web dashboard for Floci | | `http://floci-ui.local/` | |

!!! warning "MinIO's image is no longer published"
    `minio/minio` has been removed from Docker Hub and quay.io, so `minio`
    only starts on a machine that already has its image in Docker. See the
    [0.9.0 release notes](../releases/0.9.0.md#known-issue-minios-image).

Manifests for SQL Server (`mssql`, port 1433) and ProxySQL also ship in
`~/.auto/k3s/`, but they aren't in the default `local.yaml`. To use one,
add an entry for it as described [below](#add-your-own).

These credentials are for local development only. They are the same on every
machine, which is the point: every developer's config works on every other
developer's cluster.

## Starting them

**From a pod (recommended).** List what the pod needs in its
`.auto/config.yaml`. Starting the pod starts them, and auto creates the
databases and buckets before the pod starts:

```yaml title=".auto/config.yaml"
system-pods:
  - name: mysql
    databases:
      - name: shop
      - name: shop_test
  - name: postgres
    databases:
      - name: reporting
  - name: minio
    buckets:
      - name: shop-uploads
  - name: redis
  - name: mailpit
```

Cloning a repo that needs Postgres is then all it takes to get Postgres. Two
pods that ask for the same system pod share one instance, each with its own
databases.

**Always on.** Set `active: true` on the system pod's entry in
`~/.auto/config/local.yaml`:

```yaml title="~/.auto/config/local.yaml"
system-pods:
  - pod:
      name: mailpit
      active: true
      commands:
        - kubectl apply -f ~/.auto/k3s/mailpit/deployment.yaml
        - kubectl apply -f ~/.auto/k3s/mailpit/service.yaml
        - kubectl apply -f ~/.auto/k3s/mailpit/ingress.yaml
```

`commands` are what auto runs to start it, in order.

## Their data

Database data lives inside the cluster's nodes. It survives `auto stop` and
`auto start`, and it is deleted with the cluster by
`auto stop --delete-cluster`. Keep your seed scripts current and
`auto seed <pod>` brings it back. Mailpit and Floci keep everything in memory,
so a restart empties them.

If a database's port is already taken on your machine (say a MySQL you
installed yourself is on 3306), auto warns and skips that system pod rather
than fail the start.

## Add your own

A system pod is a name and a list of commands, so anything you can install
with `kubectl` or `helm` can be one. Put the manifests somewhere stable, add
an entry, and let pods ask for it by name:

```yaml title="~/.auto/config/local.yaml"
system-pods:
  - pod:
      name: rabbitmq
      active: false
      commands:
        - kubectl apply -f ~/source/platform/k8s/rabbitmq.yaml
```

```yaml title=".auto/config.yaml in a pod repo"
system-pods:
  - name: rabbitmq
```

Creating databases and buckets is built in for `mysql`, `postgres` and
`minio` only. Anything else a pod needs from a custom system pod belongs in
its manifests or its init script.
