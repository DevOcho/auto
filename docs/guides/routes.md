# Expose it with a route

auto serves every hostname through [Envoy Gateway](https://gateway.envoyproxy.io/),
which reads Kubernetes [Gateway API](https://gateway-api.sigs.k8s.io/)
`HTTPRoute`s. A pod's routes go in `.auto/routes.yaml` in its repo. auto
applies that file after the pod's install command and deletes it when the pod
stops.

!!! warning "Ingress objects are not served"
    Envoy Gateway does not read `kind: Ingress`. A pod that only ships an
    Ingress starts, but its URL never answers. `auto start` names every
    Ingress whose hosts no route serves. To convert one, see
    [Moving from ingress-nginx](from-ingress-nginx.md).

## Served on http and https

Copy this template to `.auto/routes.yaml` and replace the names marked
`replace`:

```yaml title="docs/templates/routes.yaml"
--8<-- "docs/templates/routes.yaml"
```

The parts that matter:

- **`parentRefs`** attaches the route to auto's one shared Gateway, `auto` in
  the `auto-gateway` namespace. It is the same in every cluster.
- **`hostnames`** lists every host the pod answers on. Each one goes on the
  local certificate, so multi-label hosts like `customer.portal.local` work
  over HTTPS without a wildcard. Each also needs an `/etc/hosts` line;
  `auto start` prints the ones you are missing.
- **`backendRefs`** is your Service and its port.
- No `tls:` block is needed. The Gateway terminates HTTPS for every host.

## HTTPS only

For a pod that must never be used over plain HTTP, use two routes: the pod on
the `https` listener only, and a redirect on the `http` listener. The redirect
is a 308, which keeps a POST a POST.

```yaml title="docs/templates/routes-https-only.yaml"
--8<-- "docs/templates/routes-https-only.yaml"
```

## Several paths, several services

One route can send different paths to different Services:

```yaml
spec:
  parentRefs:
  - name: auto
    namespace: auto-gateway
  hostnames:
  - shop.local
  rules:
  - matches:
    - path: {type: PathPrefix, value: /api}
    backendRefs:
    - name: shop-api
      port: 80
  - backendRefs:          # everything else
    - name: shop-web
      port: 80
```

Gateway API also does header matches, redirects, rewrites (`URLRewrite`),
request and response header changes, and traffic splits between Services.
The [HTTPRoute guide](https://gateway-api.sigs.k8s.io/guides/http-routing/)
covers them.

## Timeouts and body sizes

auto's Gateway is set up for development: no overall cap on a request, an hour
before a silent request is cut, and no limit on request bodies, so your app's
own limits apply. Most routes need nothing else. When one does:

- **Cap a slow route:** add `timeouts: {request: 300s}` to the rule.
- **Refuse large bodies at the proxy:** a `BackendTrafficPolicy` with
  `requestBuffer.limit`. The template above has it, commented out. It must
  keep `mergeType: StrategicMerge`. Without it, the policy replaces the
  Gateway's settings for that route instead of adding to them, and Envoy's
  15-second default request timeout comes back.

[Gateway settings](../reference/gateway.md) lists every default and why it was
chosen.

## Keeping an Ingress for auto 0.8 users

auto 0.8 and older never read `.auto/routes.yaml` and keep using the Ingress
in the pod's main manifest. So while a team upgrades, a repo can carry both.
auto 0.9 doesn't warn about an Ingress whose hosts a route already serves.

Keep HTTPRoutes out of the main manifest. A cluster made by auto 0.8 has no
Gateway API CRDs, and an HTTPRoute there fails the whole install.

## Checking a route

```bash
kubectl get httproute                          # is it there?
kubectl describe httproute your-service        # Accepted and ResolvedRefs should be True
curl -kI https://your-service.local            # does it answer?
kubectl logs -n envoy-gateway-system deploy/auto-envoy -c envoy   # one JSON line per request
```

A route that is `Accepted` but whose `ResolvedRefs` is `False` names a Service
or port that doesn't exist.
