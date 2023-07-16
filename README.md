# `auto`
Easily manage your k3s/k3d local development environment using k8s yaml configs or helm charts.

`auto` sets up a k3s environment utilizing k3d (k3s in docker).  It then uses a single config file
to create your environment.

## Install `auto`

### Dependencies
You will need a Linux system with the following pre-installed:
- Bash (`auto` uses Bash commands)
- Git
- Python 3
- Docker (both the daemon running and the bash command available as a non-root user)
- K3D (k3d.io)
- kubectl (working in Bash)

Optional dependencies:
- Helm (if you plan to use helm charts for deployments)

### Install Commands

NOTE: `auto` is installed for a user and not installed system wide.
You can install it with the following commands:

```bash
git clone git@github.com:DevOcho/auto.git
cd auto
./install_auto.sh
```

The `auto` install will update your `~/.bashrc` file to add itself to your path environment
variable.  For that change to take effect you will need to run `source ~/.bashrc` in each
open terminal or restart your terminals.

## Quickstart

Once you've installed `auto` you can get up and running with the following steps:

```bash
auto start
```

## Customizing and running your code

### Edit the ~/.auto/config/local.yaml file

The `local.yaml` file tells auto about your desired local environment.  `auto`
checks the "pods" section to see which portals and services you want to run as
pods in your local k3s cluster.

#### Custom Pod running your code (e.x. your project)

Here is an example we built of a web application pod:

```yaml
pods:
  - repo: git@github.com:DevOcho/portal.git
    branch: main
```

In the repo there will need to be an .auto folder that tells `auto` how to run
that pod.  See the "Setting up your application to run in auto" section below
for more information.

## System Pods

There is also a config section for the system pods that you want to run.  A system
pod might be something like a database, or other service, that your applications
need to run properly.  We define these in the auto config file so we know how to
start them but we typically ask them to start via individual repo config files.

So for example, we might want a MySQL database, so we configure it in the ~/.auto/config/local.yaml
so we know what image, what settings, etc.  Then we will tell `auto` to start it via the config.yaml
in a local repo.  That allows us to also tell `auto` what databases we need created when a new cluster
is created.

## Setting up your application to run in auto

`auto` assumes a microservices environment (but doesn't specifically require it).  With that assumption, we need
each pod to contain the configs needed to run it.  Each pod needs a git code repository.  We will look for
the following files/folders in your repo.

```
/Dockerfile
/auto/k8s   (if using k8s yaml)
/auto/helm  (if using helm charts)
/migrations (database related items [more on this later])

## Usage

Need to write this but for now just use the `auto -h` inline help docs.
