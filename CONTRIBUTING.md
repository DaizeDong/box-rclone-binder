# Contributing

Thanks for your interest in box-rclone-binder.

## Ground rules

1. **Never commit a secret.** No tokens, `config.json`, private keys, `*.env`, or filled
   `machines.yaml`. Pointers (`*_ref`) only. The gate's secret-hygiene signal (S6) will fail a PR
   that does. `verify-config` must stay a hard fail on inline secrets.
2. **The acceptance gate must stay green.** Run it before every PR:
   ```bash
   cd skills/box-rclone-binder && python tests/run_gate.py
   ```
   All 10 signals must pass with no real Box credentials. Add a signal when you add behaviour.
3. **Keep secrets referenced, health read-only, writes atomic, self-heal bounded** (see
   `PHILOSOPHY.md`). Changes that violate these are out of scope by design.
4. **Conform to Skill Repo Spec v1.** Keep the four version sources (plugin.json / both READMEs'
   roadmap badge / ROADMAP / CHANGELOG) in lock-step. Bump CHANGELOG on every change.

## Dev setup

Stdlib-only (optional PyYAML; the bundled `yamlmin` is the fallback). Python 3.8+.

```bash
git clone https://github.com/DaizeDong/box-rclone-binder.git
cd box-rclone-binder/skills/box-rclone-binder
python tests/run_gate.py
```

## PR checklist

- [ ] gate green (`tests/run_gate.py`)
- [ ] no secret material (S6)
- [ ] versions synced + CHANGELOG updated
- [ ] docs (SKILL.md / reference shards) updated if behaviour changed
