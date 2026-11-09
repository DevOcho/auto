# Moving from ingress-nginx

ingress-nginx was retired in March 2026. auto 0.9 replaced it with
[Envoy Gateway](https://gateway.envoyproxy.io/), which serves Gateway API
`HTTPRoute`s and does not read `kind: Ingress`. This page converts a pod's
Ingress, maps the nginx annotations, and lists what behaves differently.

## Convert an Ingress

Take a typical Ingress:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: shop
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: 50m
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
spec:
  rules:
  - host: shop.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: shop
            port:
              number: 80
```

The same thing as an HTTPRoute, in `.auto/routes.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: shop
  namespace: default
spec:
  parentRefs:
  - name: auto
    namespace: auto-gateway
  hostnames:
  - shop.local
  rules:
  - backendRefs:
    - name: shop
      port: 80
```

- `rules[].host` becomes `hostnames`. List every host; each goes on the
  local certificate.
- A `path: /` with `Prefix` is the default match, so it can be left out. For
  other paths, add `matches: [{path: {type: PathPrefix, value: /api}}]`.
- There is no `tls:` section and no `ingressClassName`. `parentRefs` attaches
  the route to auto's Gateway, which terminates HTTPS for every host.
- Both annotations can be dropped, as the next section explains.

Keep the Ingress in the main manifest while anyone on your team is still on
auto 0.8. See [Keeping an Ingress](routes.md#keeping-an-ingress-for-auto-08-users).

## Annotations

| ingress-nginx annotation | With auto's Envoy Gateway |
| --- | --- |
| `proxy-body-size` | Usually drop it. Envoy streams request bodies with no limit, so your app's own limit applies. To refuse large bodies at the proxy, add a `BackendTrafficPolicy` with `requestBuffer.limit` (in the [route template](routes.md#timeouts-and-body-sizes)). It must set `mergeType: StrategicMerge`, explained [below](#the-one-gotcha-policies-replace-unless-they-merge). |
| `proxy-read-timeout`, `proxy-send-timeout` | Usually drop it. A silent request may wait an hour (`streamIdleTimeout: 3600s`, the Gateway's equivalent of `proxy-read-timeout`), and there is no overall cap. To cap a route, add `timeouts: {request: 300s}` to its rule. |
| `proxy-connect-timeout` | Usually drop it: Envoy allows 10 s to connect to the pod. For another value, a `BackendTrafficPolicy` with `timeout.tcp.connectTimeout` and `mergeType: StrategicMerge`. |
| `force-ssl-redirect`, `ssl-redirect` | Use the [HTTPS-only template](routes.md#https-only): the route on the `https` listener and a 308 redirect on `http`. |
| `rewrite-target` | `rewrite-target: /` on a `/` path does nothing; drop it. A real rewrite is a `URLRewrite` filter on the rule. |
| `affinity: cookie` | A `BackendTrafficPolicy` with `loadBalancer.type: ConsistentHash` and a cookie hash, with `mergeType: StrategicMerge`. Rarely needed with one replica. |
| `auth-url` | A `SecurityPolicy` with `extAuth.http` pointing at your auth service. |
| `auth-signin` | No separate setting: the auth service answers an unauthenticated request with the login redirect itself (Authentik's Envoy endpoint does). |
| `proxy-next-upstream*` | No equivalent is set. Envoy doesn't retry; see below. |

## What behaves differently

| | ingress-nginx (auto 0.8) | Envoy Gateway (auto 0.9) |
| --- | --- | --- |
| Request body limit | 1 MiB unless annotated (413 above it) | None; your app's own limit applies |
| Silent request | Cut after 60 s unless annotated, and GETs retried twice | Waits up to an hour; never retried |
| Long request while a pod starts or a route changes | Dropped about 4 minutes later, when nginx reloads | Unaffected |
| Idle websocket | Closed after 60 s | Open for up to an hour |
| HTTP to HTTPS redirect | `force-ssl-redirect` annotation (308) | A redirect route (308) |
| New hostname's certificate | Restarted the ingress controller | Picked up live |

Each row was measured on both, side by side. The [Gateway settings](../reference/gateway.md)
page has the numbers.

## The one gotcha: policies replace unless they merge

Envoy Gateway configures timeouts, limits and load balancing with
*policies* attached to the Gateway or to a route. auto's Gateway has a
`BackendTrafficPolicy` that removes Envoy's default 15-second cap on every
request.

A policy attached to a route **replaces** the Gateway's policy for that route,
unless it sets `mergeType: StrategicMerge`. So a route that adds only a body
limit silently gets Envoy's 15-second cap back:

| Route's own policy | 2 MB upload | Request silent for 20 s |
| --- | --- | --- |
| none | 200 | 200 after 20 s |
| `requestBuffer.limit: 1Mi` | 413 | **504 after 15 s** |
| `requestBuffer.limit: 1Mi` and `mergeType: StrategicMerge` | 413 | 200 after 20 s |

Nothing in the route's own status shows this. Only the Gateway's policy reports
`Overridden=True` for that route. Give every route-level
`BackendTrafficPolicy` `mergeType: StrategicMerge`.
