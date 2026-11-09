# Databases and system pods

auto ships ready-made manifests for the services your pods depend on. They
are called *system pods*. Each one is off until something asks for it.

## What ships

| System pod | What it is | From your pods | From your machine | Credentials |
| --- | --- | --- | --- | --- |
| `mysql` | MySQL 8.0 | `mysql:3306` | `localhost:3306`, or `auto mysql` for a shell | `root` / `password` |
| `postgres` | Postgres 18 | `postgres:5432` | `localhost:5432`, or `auto postgres` | `root` / `password`, database `postgres` |
| `redis` | Redis 7.2, append-only file on | `redis:6379` | `localhost:6379` | none |
| `mailpit` | SMTP catch-all with a web inbox | `mailpit:1025` (SMTP) | `http://mailpit.local/`, or `auto mailpit` | none ([guide](mailpit.md)) |
| `floci-aws` | [Floci](https://floci.io): S3 and other AWS services, emulated (region `us-east-1`) | `http://floci-aws:4566` | `http://floci-aws.local/` | any access key and secret |
| `floci-ui` | Web dashboard for Floci | | `http://floci-ui.local/` | |

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
  - name: floci-aws
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
`auto seed <pod>` brings it back. Floci's S3 buckets and objects are kept the
same way. Mailpit and Floci's other services keep everything in memory, so a
restart empties them.

## S3 with Floci

`floci-aws` is auto's S3. Point your AWS SDK at it with any access key:

```bash
AWS_ENDPOINT_URL=http://floci-aws:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_REGION=us-east-1
```

- Use path-style addressing (`http://floci-aws:4566/<bucket>/<key>`). A
  bucket-name hostname like `uploads.floci-aws` doesn't resolve in the
  cluster. In boto3 that is `Config(s3={"addressing_style": "path"})`; in the
  JavaScript SDK, `forcePathStyle: true`.
- Objects can be read without credentials, from pods and from the browser at
  `http://floci-aws.local/<bucket>/<key>`, so an `<img>` tag can point
  straight at one.
- The same endpoint serves Floci's other AWS services, such as SQS, SNS,
  DynamoDB and Lambda.

**Coming from MinIO.** auto used to ship MinIO, which no longer publishes its
image. A pod that still asks for `minio` gets `floci-aws`, with its buckets,
and MinIO's old addresses keep working: `http://minio:9000` from pods,
`http://minio.local/` from your machine, and the `minio` / `minio123` keys.
Switch the pod to `floci-aws` when convenient; MinIO's console and `mc admin`
commands have no equivalent. MinIO's files don't move over by themselves; the
[0.9.0 upgrade steps](../releases/0.9.0.md#upgrading) show how to copy them.

## Ports already in use

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
`floci-aws` only. Anything else a pod needs from a custom system pod belongs in
its manifests or its init script.
