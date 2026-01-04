# `auto`

Easily manage your k3s/k3d local development environment using k8s YAML
configs or helm charts.

`auto` sets up a local k3s environment utilizing k3d (k3s in docker).  It then
uses config files to create your environment.  At DevOcho we have the goal
of a sub 10 minute start up for a developer joining a project.  `auto` helps us
achieve that goal.  We explain our process a bit more at the bottom of
the README.  One amazing benefit of this less than 10 minute start, is that
if anything obscure breaks, the developer is typically able to recover the
entire local development environment in 10 minutes.  This is a huge boost to
productivity.

Features:
 - Kubernetes local development environment
 - Nginx ingress
 - HTTPS support for local development
 - Quick access to databases installed in the cluster (i.e. mysql, postgres, minio, etc.)
 - Shell autocompletion for commands and pod names

Made with love by [DevOcho - Custom Software](https://www.devocho.com)

## Install `auto`

The following instructions are assuming Linux or Windows WSL2 running Bash.

### Dependencies
You will need a Linux system with the following pre-installed:
- Bash (`auto` uses Bash commands)
- Git
- Python 3
- Docker (both the daemon running and the bash command available as a non-root user)
- K3D (k3d.io)
- kubectl

Optional dependencies:
- Helm (if you plan to use helm charts for deployments)
- mkcert (if you plan to use HTTPS/SSL locally)
- libnss3-tools (required by mkcert on Linux)

### Install Commands

You can install it with the following commands:

```bash
curl -fsSL https://www.devocho.com/auto.sh | bash
```

NOTE: `auto` is installed for a user and not installed system wide.

The `auto` install will update your `~/.bashrc` file to add itself to your path
environment variable.  For that change to take effect you will need to run
`source ~/.bashrc` in each open terminal or restart your terminals.

This is what we did in your .bashrc file:

```bash
# Adding auto to the path
export PATH="$PATH:/home/$USER/.auto"
```

If you are using a shell other than Bash, you will want to add the `~/.auto`
folder to your path.

You can verify `auto` is installed with the following command:

```bash
auto --version
```

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

#### Enabling HTTPS

If you want your local cluster to run with SSL/HTTPS enabled, add the following line to your config:

```yaml
# Enable https in local development?
https: true
```

*Note: This requires `mkcert` to be installed on your system.*

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

When `https: true` is set in your `local.yaml`, `auto` will automatically:
1. Generate a local Certificate Authority (CA) using `mkcert`.
2. Generate SSL certificates for `localhost`, `*.local`, and your specific pod names (e.g., `portal.local`).
3. Configure the Nginx Ingress Controller in the cluster to use these certificates.
4. Expose port `443` on the load balancer.

**Prerequisites:**
You must install `mkcert` and `certutil` (often found in `libnss3-tools`) for this to work.
*   **Ubuntu/Debian:** `sudo apt install libnss3-tools` and follow mkcert installation instructions.
*   **Fedora:** `sudo dnf install nss-tools`
*   **Arch:** `sudo pacman -S nss`

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

### `auto stop`

Stop the cluster.

Optionally you can `--delete-cluster` to remove the entire cluster from
your machine.

### `auto restart <pod>`

This will remove and recreate the pod in the cluster.  This is nice if you are
working on the config or Dockerfile.

### `auto autocomplete`

Setup shell integration for tab completion.

### `auto images` (or `auto container-list`)

Scans the running cluster and outputs a YAML list of container images. You can copy this output into the `registry:` section of your `local.yaml` to speed up cluster startup by pre-loading images.

### `auto mysql`

Start a MySQL shell to the service MySQL pod in your cluster.  Nice for creating
databases or quick debugging.

### `auto init <pod>`

This is a convenience method for running an initialize script in your pod that
can reset the database back to it's initial configuration (before seed data
and before migrations).

### `auto seed <pod>`

This is a convenience method for running a database seed script in your pod
that will provide test data.

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

## Sharing the auto configs with your team

One frequent question we get is how do you share the auto configs with
your team?  We typically have multiple teams working on projects so
having multiple repos solved several problems for us but where do you
put the "global" auto config?

We do that with a specific repository for all of the
microservices in a project.  We call the the "project repo" and it
contains the config files for auto, a simple make process, and also
contains the docs that explain the project as a whole with overviews of the
different microservices.

When a new software developer is joining the group, they will simply do
the following:

1. Install Auto
2. Clone the "parent" repository with the auto config
3. Run `make && make install` which loads the config in the ~/.auto/config folder
4. Run `auto start`

Auto will automatically clone all the git repositories, download docker images
and populate the local registry.  It typially takes less than 10 minutes for
the developer to have everything they need on even the largest projects.
