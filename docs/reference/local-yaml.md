# local.yaml

`~/.auto/config/local.yaml` describes your environment. auto writes a default
one on install, and `auto update` keeps yours. A team usually shares one
through a [project repo](../guides/teams.md).

```yaml
code: ${HOME}/source/devocho
https: true
agents: 1
pods:
  - repo: https://github.com/DevOcho/portal.git
    branch: main
registry:
  - image: mysql:8.0
system-pods:
  - pod:
      name: mysql
      active: false
      commands:
        - kubectl apply -f ~/.auto/k3s/mysql/deployment.yaml
extra-args: ""
```

## `code`

The folder auto clones your pods' repos into, one subfolder per repo. It is
mounted into the cluster at `/mnt/code`. `~` and environment variables such as
`${HOME}` are expanded. If it doesn't exist, auto offers to create it.

Changing it takes effect for a new cluster, because the mount is made when the
cluster is created.

## `https`

`true` (the default): every pod is served on ports 80 and 443 with a locally
trusted certificate. Needs mkcert. See [HTTPS](../guides/https.md).

`false`: plain HTTP on port 8088. Takes effect for a new cluster.

## `pods`

The services to run, each in its own git repository.

| Key | |
| --- | --- |
| `repo` | Clone URL, SSH or HTTPS. The repo's name (`portal` from `.../portal.git`) is the pod's name everywhere in auto. |
| `branch` | Branch to clone and pull. Without it, the repo's default branch. |

Each repo needs a [`.auto/config.yaml`](pod-config.md).

## `registry`

Images to keep in the local registry so a new cluster doesn't download them
again:

```yaml
registry:
  - image: mysql:8.0
  - image: registry.example.com/team/service:1.4
```

auto adds every third-party image it finds running after each `auto start`, so
you rarely edit this by hand. The cluster's nodes also look up the Docker Hub
images they pull for themselves (k3s's own pods and Envoy Gateway) in this
registry first, through a `docker.io` mirror in `~/.auto/k3s/registries.yaml`.

## `system-pods`

The databases and shared services auto can start. The default file lists
every one that ships with auto. See
[Databases and system pods](../guides/system-pods.md).

| Key | |
| --- | --- |
| `pod.name` | The name pods use to ask for it in their `.auto/config.yaml`. |
| `pod.active` | `true` to always start it. Otherwise it starts only when a pod asks for it. |
| `pod.commands` | Commands that start it, run in order. |

## `agents`

Number of agent nodes besides the server node. Default `1`. `0` gives a
single-node cluster, which is not faster to start: every image pull and
database start then lands on one node. Takes effect for a new cluster.

## `extra-args`

Flags passed straight to `k3d cluster create` when a cluster is created:

```yaml
extra-args: "--k3s-arg '--disable=metrics-server@server:*'"
```
