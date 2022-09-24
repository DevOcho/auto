# `auto`
Easily manage your k3s/k3d local development environment using helm charts or k8s yaml configs.

`auto` sets up a k3s environment utilizing k3d (k3s in docker).  It then uses a single config file
to create your environment.

## Install `auto`

You will need a Linux system with the following pre-installed:
- bash (auto uses Bash commands)
- docker (both the daemon running and the bash command)
- k3d (k3d.io)

NOTE: `auto` is installed for a user and not installed system wide.
You can install it with the following commands:

```bash
git clone git@github.com:DevOcho/auto.git
cd auto
make && make install
```

Auto will update your `~/.bashrc` file to add itself to your path environment variable.  For that change to take
effect you will need to run `source ~/.bashrc` in each open terminal or restart your terminals.  This is a
one time step.

## Quickstart

Once you've installed `auto` you can get up and running with the following steps:

### Edit the ~/.auto/config/local.toml file

The `local.toml` file tells auto about your desired local environment.  `auto` checks the `[pods]` section
to see which portals and services you want to run in pods in your local k3s cluster.

Here is an example of a web application pod:

```toml
[pods]

    [pods.portal]
    # helm install --set ingress.enabled=true --description "Portal" portal portal/
    name = "portal"
    desc = "Portal"
    version = "1.0.0"
    image = "portal:1.0.0"
    active = true
    command = "helm install"
    command_args = "--set ingress.enabled=true"
    helm_directory = "portal"
    code_path = "/home/rogue/source/pyatt-dev"
    seed_command = "loaddb.py"
```

Notice in the pod definition that it is looking for an image name.  This image needs to be in your local k3s image
registry.   `auto`provides and easy method for building/taging/pushing images into the local registry.

The command is the `auto tag` command.  Below is an example of preparing the "portal" application and loading
it into the registry

```bash
cd /path/to/your/code
auto tag portal 1.0.0
```
