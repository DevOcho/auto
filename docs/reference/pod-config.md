# .auto/config.yaml

Each pod's repo has an `.auto/config.yaml` that tells auto how to deploy it.
[Add your app](../guides/add-your-app.md) walks through writing one.

```yaml
name: shop
desc: Storefront and checkout
version: 0.4.2

command: helm install
command-args: --set ingress.enabled=false

init-command: scripts/init_db.py
seed-command: scripts/seed_db.py

system-pods:
  - name: mysql
    databases:
      - name: shop
  - name: floci-aws
    buckets:
      - name: shop-uploads
  - name: mailpit
```

## Keys

| Key | Required | |
| --- | --- | --- |
| `name` | | The Helm release name. Defaults to the repo's name. |
| `desc` | | A short description, passed to Helm as the release description. |
| `version` | Yes | The image tag. The image is built when the registry doesn't have this version yet, so bump it when the `Dockerfile` or dependencies change. |
| `command` | Yes | `helm install` or `kubectl apply`. |
| `command-args` | | Arguments for `command`. `command_args` also works. |
| `init-command` | | Script that resets the database, run by `auto init` and `auto seed`. A path inside the repo. |
| `seed-command` | | Script that loads test data, run by `auto seed` after `init-command`. A path inside the repo. |
| `system-pods` | | The system pods this pod needs; see below. |

## `command`

**`helm install`** installs the chart in `.auto/helm`. The full command is:

```text
helm install <command-args> --description "<desc>" <name> <code>/<repo>/.auto/helm
```

**`kubectl apply`** runs `kubectl apply <command-args>` from the repo's
folder, so `command-args: -f .auto/deployment.yaml` applies that file. The
command runs as written: anything `kubectl apply` accepts works, including a
folder or several `-f` flags.

auto applies `.auto/routes.yaml` after either one, if the repo has it. See
[Expose it with a route](../guides/routes.md).

## `init-command` and `seed-command`

Each runs in a short-lived pod that copies the pod's Deployment (same image,
environment and volumes), as `/mnt/code/<repo>/<script>`. So the script must
be executable, with a `#!` line, and the Deployment must mount the code
folder at `/mnt/code`.

## `system-pods`

| Key | |
| --- | --- |
| `name` | A system pod from `local.yaml`: `mysql`, `postgres`, `redis`, `mailpit`, `floci-aws`, `floci-ui`, or one you added. `minio` still works and gets `floci-aws`. |
| `databases` | For `mysql` and `postgres`: databases to create, each `- name: <db>`. |
| `buckets` | For `floci-aws`: S3 buckets to create, each `- name: <bucket>`. |

Starting the pod starts these first, and its databases and buckets exist
before the pod does.

## Other files in `.auto/`

| File | |
| --- | --- |
| `routes.yaml` | The pod's HTTPRoutes, applied after it is installed and deleted when it stops. |
| `helm/` | The chart, for `command: helm install`. |
| Anything else | Your manifests, referenced from `command-args`. `deployment.yaml` is the usual name. |
