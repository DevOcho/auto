# Troubleshooting

When in doubt, start with:

```bash
auto status              # what's running
auto logs <pod>          # what the pod says
kubectl get pods -A      # including auto's own pods
```

And remember the reset button: `auto stop --delete-cluster`, then
`auto start`, gives you a clean cluster in a few minutes. Your code and cached
images are kept; re-seed databases with `auto seed <pod>`.

## `auto start` stops early

**"No registry entry in /etc/hosts"**
: Add `127.0.0.1 k3d-registry.local` to `/etc/hosts`. It is needed once.

**"Docker is missing!", "mkcert is not installed" and similar**
: Install the tool it names; see [what you need first](getting-started/install.md#what-you-need-first).
  With HTTPS on, that includes mkcert and certutil.

**"Docker Daemon doesn't appear to be running" or "The `docker` command doesn't appear to be working!"**
: Start Docker. On Linux, your user also needs to run `docker` without
  `sudo`: follow Docker's
  [post-install steps](https://docs.docker.com/engine/install/linux-postinstall/).

**"This cluster predates Envoy Gateway and has to be recreated"**
: Clusters made by auto 0.8 run ingress-nginx on Kubernetes 1.31, which
  Envoy Gateway doesn't support. Back up anything you need, then recreate it:
  `auto stop --delete-cluster` and `auto start`. The
  [0.9.0 release notes](releases/0.9.0.md#upgrading) have backup commands.
  To keep the old cluster for now, `auto update 0.8.3`.

**"Failed to create k3d cluster"**, with ports in the output
: Something else on your machine is using port 80 or 443 (or 8088 with
  `https: false`), or 6550 or 12345. Stop it, or find it with
  `sudo lsof -i :443`.

**"too many open files" or "inotify" errors** (Linux)
: The kernel's limit on inotify instances is too low for k3s, usually because
  other clusters or containers already use them. Raise it, and make it last
  past a reboot (needs `sudo`):

    ```bash
    sudo sysctl fs.inotify.max_user_instances=512
    echo fs.inotify.max_user_instances=512 | sudo tee /etc/sysctl.d/99-auto.conf
    ```

## A URL doesn't work

**The browser can't find the host**
: Each hostname needs a line in `/etc/hosts`, such as
  `127.0.0.1 shop.local`. `auto start` prints the ones you are missing.

**The host is found but nothing answers, or the response is a 404**
: The pod has no route for that host. A pod that ships only an Ingress
  starts fine, but Envoy Gateway doesn't read Ingresses, and `auto start`
  names each one. Add `.auto/routes.yaml` ([how](guides/routes.md)). Check
  what exists with `kubectl get httproute`.

**503, or "no healthy upstream"**
: The route is fine but the pod isn't ready. `auto status` and
  `auto logs <pod>` say why. `kubectl describe httproute <name>` showing
  `ResolvedRefs: False` means the route names a Service or port that doesn't
  exist.

**504 after a while, or a long request cut off**
: auto doesn't cut requests at the proxy unless the route set a timeout, so
  check the route's `timeouts` and any BackendTrafficPolicy on it. A
  route-level policy without `mergeType: StrategicMerge` brings back Envoy's
  15-second cap ([why](guides/from-ingress-nginx.md#the-one-gotcha-policies-replace-unless-they-merge)).

**The browser says the certificate isn't trusted**
: Run `mkcert -install` and restart the browser. Firefox needs certutil
  installed when mkcert runs. A brand-new hostname is added to the
  certificate when its pod starts; reload after `auto start` finishes.

The proxy's access log says what happened to each request:
`kubectl logs -n envoy-gateway-system deploy/auto-envoy -c envoy`. See
[logs and metrics](reference/gateway.md#logs-and-metrics).

## A pod misbehaves

**My code changes don't show up**
: If the pod mounts the code folder and runs a reloading dev server, they
  should appear on save. Check the Deployment's `workingDir` and volume
  ([how](guides/add-your-app.md#3-the-deployment)). Changes baked into the
  image need a rebuild: `auto tag <pod> -r`.

**"Not pulling ... because there are untracked changes"**
: auto never pulls over your work. Commit or stash in that repo and run
  `auto start` again, or pull it yourself.

**The pod crash-loops on startup**
: `auto logs <pod>`. A missing table usually means the database was never
  seeded: `auto seed <pod>`. Seeding runs in a separate pod, so it works even
  while the app is crash-looping.

**"WARNING: Port 3306 (MySQL) is already in use", and the database isn't started**
: A database you installed yourself is on the same port (3306, 5432, 6379
  or 1433). Stop it, or let your pods use it from the cluster instead.

**Pushes to the registry are refused**
: auto warns if `k3d-registry.local` resolves somewhere other than
  `127.0.0.1`. Fix the `/etc/hosts` line, or add the registry to Docker's
  `insecure-registries` as the warning shows.

## Still stuck

[Open an issue](https://github.com/DevOcho/auto/issues/new/choose) with
`auto --version`, your OS and the output of the command that failed.
