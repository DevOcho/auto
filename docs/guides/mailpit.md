# Catch email with Mailpit

Pods that send email can deliver it to [Mailpit](https://mailpit.axllent.org/)
instead of a real mail provider. You read every message in a browser, and
nothing ever reaches a real inbox.

## Ask for it

```yaml title=".auto/config.yaml"
system-pods:
  - name: mailpit
```

## Point your app at it

There is no TLS and no authentication, so turn both off. Mailpit accepts any
credentials you do send.

```text
EMAIL_HOST=mailpit
EMAIL_PORT=1025
EMAIL_USE_TLS=False
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
```

The names differ by framework. What matters is host `mailpit`, port `1025`,
no TLS.

## Read the mail

Open <http://mailpit.local/> (or `https://mailpit.local/` with HTTPS on). It
needs the usual `/etc/hosts` line, which `auto start` prints if it is
missing:

```text
127.0.0.1 mailpit.local
```

To skip the hosts entry, `auto mailpit` forwards the inbox to
<http://127.0.0.1:8025/> until you press ctrl+c.

## What to expect

- Messages live in the pod's memory and are gone when it restarts. That keeps
  yesterday's experiments out of today's inbox.
- The inbox keeps the newest 500 messages.
- Mailpit also has an [API](https://mailpit.axllent.org/docs/api-v1/), which is
  handy for tests that check an email was sent.
