# Contributing

This repository is retired from active development. Keep maintenance changes focused on existing installations and retained recovery needs.

## Ground rules

1. **Keep private data out of this public repository.** No tokens, `config.json`, private keys, `*.env`, or filled
   `machines.yaml`. Pointers (`*_ref`) only. `verify-config` must stay a hard fail on inline secrets.
2. **Keep secrets referenced, health read-only, writes atomic, and recovery explicit** (see
   `PHILOSOPHY.md`). Changes that violate these are out of scope by design.
3. **Conform to Skill Repo Spec v1.** Keep version sources (plugin.json / both READMEs'
   roadmap badge / ROADMAP / CHANGELOG) in lock-step. Bump CHANGELOG on every change.

## Dev setup

Stdlib-only (optional PyYAML; the bundled `yamlmin` is the fallback). Python 3.8+.

```bash
git clone --recurse-submodules https://github.com/DaizeDong/box-rclone-binder.git
```

## PR checklist

- [ ] no secret material
- [ ] versions synced + CHANGELOG updated
- [ ] docs (SKILL.md / reference shards) updated if behaviour changed
