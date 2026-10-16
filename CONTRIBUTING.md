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
git clone https://github.com/DaizeDong/box-rclone-binder.git
git -C box-rclone-binder config core.hooksPath .githooks
```

The hooks run the privacy and dash checks from local fleet-guards and fleet-style checkouts named by `git config --global fleet.guards` and `fleet.style`; the companion resolver needs `pip install -e "$(git config --get fleet.guards)"`. A commit is blocked while either is unset.

## PR checklist

- [ ] no secret material
- [ ] versions synced + CHANGELOG updated
- [ ] docs (SKILL.md / reference shards) updated if behaviour changed
