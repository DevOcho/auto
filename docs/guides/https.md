# HTTPS

HTTPS is on by default, so your pods run locally the way they run in
production: secure cookies, `https://` redirects, OAuth callbacks and browser
APIs that only work in a secure context all behave the same.

## How it works

1. auto uses [mkcert](https://github.com/FiloSottile/mkcert) to create a
   certificate authority on your machine and adds it to your system's and
   browsers' trust stores. This is done once, and can ask for your `sudo`
   password.
2. It issues a certificate for `localhost`, `*.local` and each pod's
   `<name>.local`, and stores it in `~/.auto/certs`.
3. Envoy Gateway's HTTPS listener on port 443 serves that certificate for
   every host.
4. After the pods are deployed, auto reads every hostname their routes
   declare and issues the certificate again to cover them all. A wildcard
   covers only one label, so `*.local` does not cover `portal.app.local`;
   naming each host does. Envoy picks up the new certificate live, without a
   restart.

## Install mkcert and certutil

`auto start` stops and says so if either is missing.

| System | Command |
| --- | --- |
| Ubuntu, Debian | `sudo apt install libnss3-tools`, then [install mkcert](https://github.com/FiloSottile/mkcert#installation) |
| Fedora | `sudo dnf install nss-tools`, then install mkcert |
| Arch | `sudo pacman -S nss`, and `mkcert` from the AUR (`yay -S mkcert`) |
| macOS | `brew install mkcert nss` |

## Turn it off

```yaml title="~/.auto/config/local.yaml"
https: false
```

Every pod is then served over plain HTTP on port 8088, for example
`http://portal.local:8088/`. Ports 80 and 443 are left free. This takes
effect for a new cluster: run `auto stop --delete-cluster` and `auto start`.

## Trusting the certificate outside the browser

Browsers trust it as soon as mkcert installs its authority. Some runtimes on
your machine bring their own list of trusted authorities and need to be
pointed at mkcert's:

```bash
export NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem"   # Node.js
export REQUESTS_CA_BUNDLE="$(mkcert -CAROOT)/rootCA.pem"    # Python requests
```

Inside the cluster, pods call each other by Service name over plain HTTP
(`http://your-service`). They don't go through the Gateway, so they don't
need the certificate.
