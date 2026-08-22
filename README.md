# `auto`

Easily manage your k3s/k3d local development environment using k8s YAML
configs or helm charts.

`auto` sets up a local k3s environment utilizing k3d (k3s in docker).  It then
uses config files to create your environment.  At DevOcho we have the goal
of a sub 10 minute start up for a developer joining a project.  `auto` helps us
achieve that goal.  We explain our process a bit more at the bottom of
the README.  One amazing benefit of this less than 10 minute start, is that
if anything obscure breaks, the developer is typically able to recover the
entire local development environment in less than 10 minutes.  This is a huge boost to
productivity.

Features:
 - Kubernetes local development environment
 - Nginx ingress
 - HTTPS support for local development
 - Quick access to databases installed in the cluster (i.e. mysql, postgres, minio, redis, mssql, proxysql, matomo, etc.)
 - Local email capture with mailpit (read what your pods send in a browser)
 - Shell autocompletion for commands and pod names

Made with love by [DevOcho - Custom Software](https://www.devocho.com)

## Install `auto`

`auto` runs on Linux, macOS (Apple Silicon), and Windows via WSL2.

### Dependencies
You will need one of the supported systems with the following pre-installed:
- Bash or Zsh (`auto` uses POSIX shell commands; macOS defaults to Zsh, most Linux distros default to Bash)
- Git
- Python 3
- Docker (both the daemon running and the bash command available as a non-root user)
- K3D (k3d.io)
- kubectl

Optional dependencies:
- Helm (if you plan to use helm charts for deployments)
- mkcert (if you plan to use HTTPS/SSL locally)
- libnss3-tools (required by mkcert on Linux)
- [smalls](https://github.com/DevOcho/smalls) if working with Python and Peewee

### Install Commands

You can install it with the following commands:

```bash
curl -fsSL https://www.devocho.com/auto.sh | bash
```

NOTE: `auto` is installed for a user and not installed system wide.

The installer detects your default shell (`$SHELL`) and updates the matching rc
file — `~/.bashrc` for Bash or `~/.zshrc` for Zsh (the default on macOS) — to
add itself to your path. For that change to take effect you will need to run
`source ~/.bashrc` (or `source ~/.zshrc`) in each open terminal or restart your
terminals.

This is what we add to your shell rc file:

```bash
# Adding auto to the path
export PATH="$PATH:$HOME/.auto"
```

If you are using a shell other than Bash or Zsh, the installer will write the
path entry to `~/.profile` automatically.

On macOS, the installer also clears the `com.apple.quarantine` attribute that
Gatekeeper applies to downloaded binaries, so `auto` runs on first invocation
without a "cannot verify developer" warning.

You can verify `auto` is installed with the following command:

```bash
auto --version
```

### `/etc/hosts` requirement

`auto start` requires the following entry in `/etc/hosts`:

```
127.0.0.1 k3d-registry.local
```

If this entry is missing, `auto start` will fail with an error. Add it once
and you will not need to touch it again.

## Shell Autocompletion

`auto` supports tab completion for Bash, Zsh, and Fish. This allows you to tab-complete commands, options, and even pod names (e.g., `auto start my<tab>` -> `auto start my-pod`).

To see the installation instructions for your shell, run:

```bash
auto autocomplete --shell bash  # or zsh, fish
```

For automatic installation, you can use the `--install` flag:

```bash
auto autocomplete --shell bash --install
source ~/.bashrc
```

## Quickstart

Once you've installed `auto` you can get up and running with the following steps:

### Edit the `~/.auto/config/local.yaml` file

The install process installed a config folder for you.  Inside the config
folder is the `local.yaml` file.  The `local.yaml` file tells `auto` about
your desired local environment.

#### The local code folder

You need to edit the `code` folder in the `~/.auto/config/local.yaml` file to
be a location that you want your project code to go.  By default this is
`~/source`.  If this isn't where you want things then you need to change it.
This is what I have set for mine:

```bash
# The code folder is where we will download all of your pod code repositories
code: /home/rogue/source/devocho
```

#### HTTPS

HTTPS is enabled by default in the shipped `local.yaml`:

```yaml
# Enable https in local development?
https: true
```

If you want to disable HTTPS, set it to `false`:

```yaml
https: false
```

*Note: HTTPS requires `mkcert` to be installed on your system.*

#### Adding Your Pods

`auto` checks the `[pods]` section to see which pods you want to run in
your local k3s cluster.  We assume each pod is in it's own separate git
repository.

Below is an example to show you how to setup a pod:

```yaml
pods:
  - repo: git@github.com:DevOcho/portal.git
    branch: main
```

#### The `registry:` key

The `registry:` key holds a list of Docker images to pre-load into the local
registry when the cluster starts, which significantly speeds up startup time:

```yaml
registry:
  - image: registry.company.com/project/service:tag
```

This list is auto-populated by `auto images` and is also updated automatically
after each successful `auto start`, so you rarely need to edit it by hand.

#### System Pods

`auto` ships ready-made manifests for the services your pods depend on, so you
don't have to package them yourself:

| System pod | What it gives you | Reachable at |
| ---------- | ----------------- | ------------ |
| `mysql`    | MySQL database    | `mysql:3306` (and `localhost:3306`) |
| `postgres` | Postgres database | `postgres:5432` (and `localhost:5432`) |
| `redis`    | Redis cache/broker | `redis:6379` (and `localhost:6379`) |
| `minio`    | S3-compatible object storage | `minio:9000`, console via `auto minio` |
| `matomo`   | Analytics | `http://matomo.local/` |
| `mailpit`  | SMTP catch-all + web inbox | `mailpit:1025` (SMTP), `http://mailpit.local/` (inbox) |

Manifests for `mssql` and `proxysql` ship in `~/.auto/k3s/` as well, but are
not listed in the default config.

They are all off by default. You can turn one on globally by setting `active:
true` on it in the `system-pods` section of your `~/.auto/config/local.yaml`:

```yaml
system-pods:
  - pod:
      name: mailpit
      active: true
      commands:
        - kubectl apply -f ~/.auto/k3s/mailpit/deployment.yaml
        - kubectl apply -f ~/.auto/k3s/mailpit/service.yaml
        - kubectl apply -f ~/.auto/k3s/mailpit/ingress.yaml
```

You usually don't need to do that, though. A pod that asks for a system pod in
its own `.auto/config.yaml` (see below) starts it implicitly, so cloning a repo
that needs mailpit is all it takes to get mailpit.

#### Catching Email with Mailpit

Any pod that sends email can deliver it to the `mailpit` system pod instead of
a real mail provider, and you read the messages in a browser rather than
digging through worker logs. Ask for it in your pod's `.auto/config.yaml`:

```yaml
system-pods:
  - name: mailpit
```

Then point your application's SMTP settings at it. There is no TLS and no
authentication, so turn both off (mailpit accepts any credentials you do send):

```
EMAIL_HOST=mailpit
EMAIL_PORT=1025
EMAIL_USE_TLS=False
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
```

The inbox is at [http://mailpit.local/](http://mailpit.local/) (or
`https://mailpit.local/` when `https: true`). Like every other `.local`
hostname, it needs an `/etc/hosts` entry:

```
127.0.0.1      mailpit.local
```

`auto start` lists the hostnames it deployed and prints the `/etc/hosts` lines
you are still missing, so you don't have to remember this one. If you'd rather
skip the host entry entirely, `auto mailpit` port-forwards the inbox to
<http://127.0.0.1:8025/>.

Messages live inside the pod and are dropped when it restarts, which keeps
yesterday's experiments out of today's inbox.

#### Extra cluster creation args

Use `extra-args:` to pass arbitrary flags directly to `k3d cluster create` at
cluster creation time:

```yaml
extra-args: "--agents 2 --k3s-arg '--disable=traefik@server:*'"
```

### Setting up your application to run in `auto`

`auto` assumes a microservices environment (but doesn't specifically require
it).  With that assumption, we need each pod to contain the config files needed
to run it.  Since each pod is it's own unique git code repository.  We will look
for the following files/folders in your repo:

```
/Dockerfile
/.auto/config.yaml (explained below)
/.auto/k8s   (if using k8s yaml)
/.auto/helm  (if using helm charts)
```

In your pod you will need an `.auto` folder that contains a `config.yaml`
file that tells auto how you want it to run.  Here is an example of a
web application pod using a helm chart:

```yaml
---
# Portal information
name: portal
desc: Reference Portal
version: 0.0.2

# k8s/k3s commands
command: helm install
command-args: --set ingress.enabled=true

# Database commands
seed-command: seed_db.py
init-command: init_db.py

# Configuration for the system-pods
system-pods:

  # We need a MySQL database
  - name: mysql
    databases:
      - name: www

  # We need a MinIO bucket
  - name: minio
    buckets:
       - name: www
```

You can see the repository for this example "portal" pod here:
[https://github.com/DevOcho/portal] (https://github.com/DevOcho/portal)

Once you have the config files ready, you can start the cluster and pods with the following command:

```bash
auto start
```

If you prefer to use yaml vs helm, you can change your command to something like the following:

```yaml
# k8s/k3s commands
command: kubectl apply
command-args: -f '.auto/deployment.yaml'
```

Where your entire yaml is in that file.  This command is run blindly so be careful.  With much
power comes much responsibility.

## HTTPS Support

HTTPS is enabled by default. When `https: true` is set in your `local.yaml`, `auto` will automatically:
1. Generate a local Certificate Authority (CA) using `mkcert`.
2. Generate SSL certificates for `localhost`, `*.local`, and your specific pod names (e.g., `portal.local`).
3. Configure the Nginx Ingress Controller in the cluster to use these certificates.
4. Expose port `443` on the load balancer.

After pods are installed, `auto` re-issues the TLS certificate to cover every
host declared in deployed Ingress resources. This means multi-label subdomains
like `portal.app.local` are covered automatically — not just `*.local`.

**Prerequisites:**
You must install `mkcert` and `certutil` (often found in `libnss3-tools`) for this to work.
*   **Ubuntu/Debian:** `sudo apt install libnss3-tools` and follow mkcert installation instructions.
*   **Fedora:** `sudo dnf install nss-tools`
*   **Arch:** `sudo pacman -S nss` — `mkcert` is available from the AUR: `yay -S mkcert`
*   **macOS:** `brew install mkcert nss`

On the first run, `auto start` may prompt you for your `sudo` password to install the local CA into your system's trust store.

## Usage

You can get basic help by running `auto --help`.
Thanks for your interest!

`<pod>` is the short name of the pod.  For example, the portal above might be
fully named "portal-596d876cff-pc99c".  When you see `<pod>` you can just use
"portal" and auto will look up the full name for you.

Here are the most common commands:

### `auto start`

Start the cluster and all pods.

Pass `--dry-run` to print what would happen without making any changes.
Pass `--offline` to skip git pulls and registry operations (useful when working without network access).

### `auto start <pod>`

When a pod name is given, starts only that single pod (assumes the cluster is
already running). Pulls the repo, builds the image, installs its
databases/buckets, and refreshes HTTPS if needed.

Pass `--dry-run` to preview without making changes.

### `auto stop`

Stop the cluster.

Optionally you can `--delete-cluster` to remove the entire cluster from
your machine.

Pass `--dry-run` to print what would happen without making any changes.

### `auto stop <pod>`

When a pod name is given, stops only that pod without touching the rest of the
cluster.

Pass `--dry-run` to preview without making changes.

### `auto status`

Prints a table showing cluster status, registry status, and all running pods.

Options:
- `--namespace`/`-n` — filter by namespace
- `--all-namespaces`/`-a` — show pods across all namespaces
- `--watch`/`-w` — refresh the table every 3 seconds

### `auto restart <pod>`

This will remove and recreate the pod in the cluster.  This is nice if you are
working on the config or Dockerfile.

### `auto logs <pod>`

Streams live pod logs, filtering out health-check noise. Reports pod state if
the pod is not in Running status.

### `auto autocomplete`

Setup shell integration for tab completion.

### `auto images`

Scans the running cluster and outputs a YAML list of container images. You can copy this output into the `registry:` section of your `local.yaml` to speed up cluster startup by pre-loading images.

### `auto mysql`

Start a MySQL shell to the service MySQL pod in your cluster.  Nice for creating
databases or quick debugging.

### `auto postgres`

Opens an interactive `psql` shell to the postgres system pod. Works the same
way as `auto mysql` but connects to the Postgres pod.

### `auto minio`

Port-forwards to the MinIO pod and prints the browser URL
(`http://127.0.0.1:9090/`) along with the default credentials so you can log
in immediately.

### `auto mailpit`

Port-forward the Mailpit inbox to <http://127.0.0.1:8025/> so you can read the
email your pods sent. Handy when `mailpit.local` isn't in your `/etc/hosts`, or
when you'd rather not add it. Press ctrl+c to close the forward.

### `auto init <pod>`

This is a convenience method for running an initialize script in your pod that
can reset the database back to it's initial configuration (before seed data
and before migrations).

### `auto seed <pod>`

Runs the `init-command` to reset the database, then the `seed-command` to
populate test data.

### `auto migrate <pod>`

If you use the DevOcho `smalls` migration script in your application, this
will run it inside a pod as a convenience method.

### `auto rollback <pod> <number>`

If you use the DevOcho `smalls` migration script in your application, this
will run the rollback feature inside a pod as a convenience method.

Example: ```auto rollback training 0123```

The above example will rollback the database to the 0123 migration.

### `auto tag <pod>`

This will build the local pod image, tag it, and upload it to the local
repository.

### `auto upgrade <pod>`

Rebuilds and repopulates the local container registry for the given pod.

### `auto update [VERSION]`

Update `auto` itself to the latest release.  Optionally you can pass a
version to go to that exact release instead (up or down), which is nice
if you need to roll back an update.

Example: ```auto update 0.7.1```

If the version doesn't exist, `auto` will tell you and leave your current
install untouched.  You can add `--dry-run` to preview the change without
installing anything.  The install script honors the same pin through the
`AUTO_VERSION` environment variable if you prefer to run it directly.

Pass `--force` to reinstall even when already on the latest version.

### `auto install <git_repo>`

Clones a parent/project config repo, backs up the current `local.yaml`, and
installs the repo's `local.yaml` into `~/.auto/config/`. This is the
recommended way to share `auto` configs across a team (see "Sharing configs"
below).

## Sharing the auto configs with your team

One frequent question we get is how do you share the auto configs with
your team?  We typically have multiple teams working on projects so
having multiple repos solved several problems for us but where do you
put the "global" auto config?

We do that with a specific repository for all of the
microservices in a project.  We call it the "project repo" and it
contains the config files for auto, a simple make process, and also
contains the docs that explain the project as a whole with overviews of the
different microservices.

When a new software developer is joining the group, they will simply do
the following:

1. Install Auto
2. Clone the "parent" repository with the auto config
3. Run `auto install <git_repo>` (or `make && make install`) which loads the config in the ~/.auto/config folder
4. Run `auto start`

Auto will automatically clone all the git repositories, download docker images
and populate the local registry.  It typically takes less than 10 minutes (average
in 2025 was 2 minutes) for the developer to have everything they need on even
the largest projects.
