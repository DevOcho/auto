# Gateway settings

What auto sets up to serve your pods, and why. The manifests are in
`~/.auto/k3s/envoy-gateway/`. auto 0.9 runs Envoy Gateway v1.9.3 on k3s
v1.35.9.

## The objects

| Object | Name | |
| --- | --- | --- |
| GatewayClass | `auto` | Points at the EnvoyProxy settings below. |
| Gateway | `auto`, namespace `auto-gateway` | The one entry point. Every route's `parentRefs` names it. Routes may attach from any namespace. |
| Listeners | `http` on 80, `https` on 443 | `https` only when `https: true`. It has no hostname, so it serves every host. |
| Certificate | Secret `local-tls` in `auto-gateway` | Issued by mkcert. Updated in place when hosts are added; Envoy serves the new one without a restart. |
| Proxy | Deployment and Service `auto-envoy`, namespace `envoy-gateway-system` | One replica. Requests 100m CPU and 128Mi (Envoy Gateway's default is 512Mi). The Service is a LoadBalancer with `externalTrafficPolicy: Cluster`, so every node passes traffic on. |
| Controller | `envoy-gateway`, namespace `envoy-gateway-system` | Requests 128Mi. |

## Timeouts and limits

| Setting | Value | Set by |
| --- | --- | --- |
| Time a request may sit with no bytes moving | 3600 s | ClientTrafficPolicy `auto-client`, `timeout.http.streamIdleTimeout` |
| Overall time for a request | No cap | BackendTrafficPolicy `auto-backend`, `timeout.http.requestTimeout: 0s` |
| Connecting to your pod | 10 s | Envoy's default |
| Request body size | No limit | Envoy's default; opt in per route |
| Retries | None | Envoy's default |
| Websockets | Upgraded; same 3600 s idle limit | Envoy Gateway's default |

The idle timeout is the equivalent of nginx's `proxy-read-timeout`: the time
allowed between two reads. Envoy's own default is 5 minutes. auto allows an
hour because some pods make long synchronous calls (an AI model, a report)
and a local proxy shouldn't be what cuts them off.

Envoy's default overall request timeout is 15 s, far too short for those same
calls, so auto removes it. A route that wants a cap sets its own.

## Per route

Nothing below applies unless a pod's `.auto/routes.yaml` asks for it.

| Setting | How |
| --- | --- |
| Overall request cap | `timeouts: {request: 300s}` on the HTTPRoute rule |
| Request body limit | BackendTrafficPolicy on the route, `requestBuffer.limit`, with `mergeType: StrategicMerge`. Envoy holds a limited body in memory and answers 413 above the limit. |
| A different idle timeout | BackendTrafficPolicy on the route, `timeout.http.streamIdleTimeout`, with `mergeType: StrategicMerge` |
| HTTP to HTTPS redirect | The [HTTPS-only template](../guides/routes.md#https-only) (308) |
| Path rewrite | HTTPRoute `URLRewrite` filter |

**Every route-level BackendTrafficPolicy needs `mergeType: StrategicMerge`.**
Without it, the route's policy replaces the Gateway's instead of adding to it,
and the 15-second cap comes back for that route.
[The details, measured.](../guides/from-ingress-nginx.md#the-one-gotcha-policies-replace-unless-they-merge)

`mergeType` is only allowed on a policy that targets a route.
ClientTrafficPolicy can't target a route at all, so the 3600 s idle timeout
applies to every route unless a BackendTrafficPolicy overrides it.

## Logs and metrics

Each request is one JSON line on the proxy's stdout:

```bash
kubectl logs -n envoy-gateway-system deploy/auto-envoy -c envoy -f
```

```json
{"authority":"portal.local","bytes_received":0,"bytes_sent":615,"duration_ms":1,"method":"GET","path":"/","protocol":"HTTP/2","request_id":"...","response_code":200,"response_flags":"-","start_time":"...","upstream_host":"10.42.1.7:80","user_agent":"curl/8.5.0","x_forwarded_for":"10.42.0.1"}
```

`response_flags` explains Envoy's own errors: `UF` could not connect to the
pod, `UT` timed out, `NR` no route matched the host.

Prometheus metrics are at `/stats/prometheus` on the proxy's admin port,
19001.

## Measured against ingress-nginx

Run on a two-node cluster with real pods, against auto 0.8's ingress-nginx on
the same machine:

| Check | auto 0.9 | ingress-nginx |
| --- | --- | --- |
| 2 MB upload, route with no limit | 200 | 413 (1 MiB default) |
| 80 MB upload, route copied from an Ingress with `proxy-body-size: 72m` | 200: no proxy limit, so the app's applies | 413 |
| http request to an HTTPS-only route | 308 to the same URL on https | 308 |
| Certificate for a new multi-label host | Served at once; nothing restarted | Served after the controller restarted |
| Silent 75 s request, no annotations | 200 after 75 s | 504 after 180 s (cut at 60 s, retried twice) |
| Silent 400 s request, with a route added and the certificate refreshed while it waits | 200 after 400 s | Dropped after 241 s, when the reload closed old connections |
| Websocket idle for 90 s | Open, echo works | Closed |
