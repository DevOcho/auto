# Add your app

auto runs each service from its own git repository. It calls each one a
*pod*. This guide adds a service to auto. The files below go in the service's
repo, so anyone who runs auto gets the same setup.

```text
your-service/
├── Dockerfile            # how to build the image
└── .auto/
    ├── config.yaml       # how auto deploys it
    ├── deployment.yaml   # plain Kubernetes YAML ...
    ├── helm/             # ... or a Helm chart
    └── routes.yaml       # the hostnames it answers on
```

## 1. A Dockerfile

auto builds the `Dockerfile` at the root of the repo. The image is named
after the repo and tagged with the `version` from `.auto/config.yaml`, then
pushed to the local registry:

```text
k3d-registry.local:12345/<repo-name>:<version>
```

Use that name in your Deployment. auto rebuilds the image when the version
in the registry is missing: bump `version`, or run `auto tag <pod>` to build
on demand.

## 2. `.auto/config.yaml`

=== "Plain YAML"

    ```yaml
    name: your-service
    desc: What it does, in a few words
    version: 0.1.0

    # Run from the repo's folder
    command: kubectl apply
    command-args: -f .auto/deployment.yaml
    ```

=== "Helm chart"

    ```yaml
    name: your-service
    desc: What it does, in a few words
    version: 0.1.0

    # auto adds: --description "<desc>" <name> <repo>/.auto/helm
    command: helm install
    command-args: --set image.tag=0.1.0
    ```

Every key is in the [`.auto/config.yaml` reference](../reference/pod-config.md).

## 3. The Deployment

A Deployment and a Service, as you would write them for any cluster. Two
things make it an auto pod: the image comes from the local registry, and
the code folder is mounted so the pod runs your working copy.

```yaml title=".auto/deployment.yaml"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: your-service
spec:
  replicas: 1
  selector:
    matchLabels:
      app: your-service
  template:
    metadata:
      labels:
        app: your-service
    spec:
      containers:
      - name: your-service
        image: k3d-registry.local:12345/your-service:0.1.0
        # Run the code on your machine, not the copy baked into the image,
        # with a dev server that reloads when files change.
        workingDir: /mnt/code/your-service
        command: ["flask", "run", "--host=0.0.0.0", "--port=8080", "--reload"]
        ports:
        - containerPort: 8080
        env:
        - name: DATABASE_URL
          value: mysql://root:password@mysql:3306/your_service
        volumeMounts:
        - name: code
          mountPath: /mnt/code
      volumes:
      - name: code
        persistentVolumeClaim:
          claimName: code     # auto creates this: your code folder
---
apiVersion: v1
kind: Service
metadata:
  name: your-service
spec:
  selector:
    app: your-service
  ports:
  - port: 80
    targetPort: 8080
```

The `code` claim is your whole code folder, the same one auto clones into,
so `/mnt/code/your-service` is the repo you are editing. Any framework with a
reloading dev server (Flask, Django, Rails, Next.js, nodemon, air) picks up
your changes the moment you save.

## 4. Its hostnames: `.auto/routes.yaml`

```yaml title=".auto/routes.yaml"
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: your-service
  namespace: default
spec:
  parentRefs:
  - name: auto
    namespace: auto-gateway
  hostnames:
  - your-service.local
  rules:
  - backendRefs:
    - name: your-service
      port: 80
```

auto applies this file after the pod is installed and covers each hostname
with its local certificate. [Expose it with a route](routes.md) has the
templates and options, including HTTPS-only routes.

## 5. Databases, if it needs them

Name them in `.auto/config.yaml` and auto starts the database and creates
them before your pod starts:

```yaml
system-pods:
  - name: mysql
    databases:
      - name: your_service
  - name: floci-aws
    buckets:
      - name: your-service-uploads
  - name: mailpit
```

[Databases and system pods](system-pods.md) has each one's hostname, port and
credentials.

## 6. Seed data

Point auto at scripts in your repo that reset and fill the database:

```yaml
init-command: scripts/init_db.py   # drop and recreate the schema
seed-command: scripts/seed_db.py   # load test data
```

`auto init <pod>` runs the first; `auto seed <pod>` runs both. Each runs in
a short-lived copy of your Deployment, as `/mnt/code/<pod>/<script>`, so the
script must be executable (`chmod +x`, with a `#!` line) and the Deployment
must mount the code folder as above. Because it is a separate pod, seeding
still works when the app itself is crash-looping on an empty database.

## 7. Add it to `local.yaml` and start it

```yaml title="~/.auto/config/local.yaml"
pods:
  - repo: git@github.com:your-org/your-service.git
    branch: main
```

```bash
auto start your-service   # into the running cluster
# or
auto start                # everything
```

## The daily loop

| You changed | Run |
| --- | --- |
| Application code | Nothing, if the dev server reloads. Otherwise `auto refresh <pod>`. |
| The `Dockerfile` or dependencies | `auto tag <pod> -r` (build, push, restart the pod) |
| `.auto/deployment.yaml`, the chart or `routes.yaml` | `auto restart <pod>` |
| The database schema or seed data | `auto seed <pod>` |
| Something is broken and you don't know why | `auto stop --delete-cluster` and `auto start` |
