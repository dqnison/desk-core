---
tier: link
guard:
  - deny: '(?:^|[;&|(]\s*)(?:sudo\s+)?rm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*|-r\s+-f|-f\s+-r)\s+(?:/|~|\$HOME|\*)(?:/?\*?)(?:\s|$|;)'
    id: no-rm-rf-root
    why: rm -rf on /, ~ or * — name the exact folder instead
  - ask: ['**/.env', '**/.env.*', '**/*.pem', '**/id_rsa*']
    in: paths
    id: secrets-write
    why: the call writes a secrets file (.env, keys) — say what changes and why before it runs
---

# Safety

A starter set of guards: always on while this file is in the desk, whatever the
task. Add yours below; switch one off with `enabled: false` on the file, or delete it.
Force push to a shared branch is denied by the example extension `ext/no_force_push.py`.
