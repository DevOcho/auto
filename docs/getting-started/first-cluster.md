# Your first cluster

A fresh install comes with a working example: the default
`~/.auto/config/local.yaml` lists one service,
[portal](https://github.com/DevOcho/portal), a small web server built for
trying auto. This page starts it, opens it in a browser and shows the
commands you'll use every day.

## Start it

```bash
auto start
```

The first run asks whether to create the code folder (`~/source/devocho`
unless you changed `code:` in `local.yaml`). That is where auto clones your
services. Then it:

- checks that Docker, k3d and kubectl are working,
- creates a local certificate authority with mkcert, if you don't have one yet,
- clones portal and builds its image,
- starts a local image registry and a two-node k3s cluster,
- installs Envoy Gateway, the proxy that serves every hostname,
- deploys portal and its route,
- issues a certificate that covers every hostname the routes declare.

Installing the certificate authority into your system's trust store can ask
for your `sudo` password once.

The first start downloads k3s, Envoy Gateway and the images it needs, so
give it a few minutes. auto caches all of them in its local registry. After
that, a fresh cluster starts in about two minutes and a stopped one resumes
in about 35 seconds.

## Open it

When it finishes, `auto start` lists the URLs it deployed and any
`/etc/hosts` lines you still need:

```text
127.0.0.1 portal.local
```

Add the line, then open <https://portal.local>. The certificate is trusted
because mkcert added its authority to your system and browsers.

## Look around

```bash
auto status          # cluster, registry and pods at a glance
auto status -w       # the same, refreshed every 3 seconds
auto logs portal     # follow portal's logs, without health-check noise
kubectl get pods -A  # it's a normal cluster: kubectl, k9s and Lens all work
```

`<pod>` arguments take the short name: `portal`, not
`portal-596d876cff-pc99c`. Tab completion fills them in if you
[turned it on](install.md#turn-on-tab-completion-optional).

## Stop, resume, rebuild

```bash
auto stop                   # stop the cluster; everything is kept
auto start                  # resume it
auto stop --delete-cluster  # delete it, databases included
auto start                  # a clean cluster from scratch
```

Deleting the cluster is the reset button. If something gets into a strange
state, delete and start again. Your code folder and the cached images
survive; the cluster's databases do not. Re-seed them with
`auto seed <pod>`.

## Next

- Put one of your own services in auto: [Add your app](../guides/add-your-app.md).
- Understand what just happened: [How auto works](how-it-works.md).
