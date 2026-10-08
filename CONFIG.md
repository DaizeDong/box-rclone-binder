# box-rclone-binder, Config (repo overview)

This repo ships a single skill, **`box-rclone-binder`**, which is **config-bearing**: it reads a
per-fleet machine inventory, `machines.yaml`, listing your hosts, auth mode, and **pointers** to
where secrets live (never the secret values themselves).

The authoritative, full config contract, every field, type, required-ness, example, the discovery
order, secrets Mode B, first-time setup, and hot-swap, lives in the canonical skill doc:

**→ [`skills/box-rclone-binder/CONFIG.md`](skills/box-rclone-binder/CONFIG.md)**

This root file is a thin pointer so the config standard is discoverable from the repo root; the
canonical doc is the source of truth.

## At a glance

- **Schema:** `machines.yaml`, top-level `schema_version` (the `version` field; currently `1`) +
  `defaults` / `secrets` / `hosts[]` / `alerts`. Full field tables are in the canonical doc.
- **Discovery env var:** `$BOX_RCLONE_BINDER_CONFIG` (a file, or a dir holding `machines.yaml`),
  then `$BOX_RCLONE_BINDER_CONFIG_DIR`, then the private companion resolved by
  `guards/tools/datadir.py`, including DATA_DIR, proven sibling and home fallback selection
  described in the canonical document. A missing selected inventory is a configuration error.
- **First-time (deterministic stamp):** set the env var to your private companion, then
  `python scripts/init_config.py` writes a
  `machines.yaml` byte-identical to the committed `machines.example.yaml` template.
- **Verify / hot-swap:** `python scripts/verify_config.py` resolves + validates the config the env
  var points at and prints the resolved path, so switching `$BOX_RCLONE_BINDER_CONFIG` between two
  retained companions, each with `machines.yaml`, is provable. Required reference failures
  report NOT READY with exit 3; no remote host is probed. (Root `scripts/init_config.py` and `scripts/verify_config.py` are thin shims
  delegating to `skills/box-rclone-binder/scripts/`.)
- **Secrets, Mode B:** the live `machines.yaml` carries only `*_ref` pointers; real values live in
  your backend (`env`/`file`/`op`/`vault`/`aws-ssm`). `.gitignore` blocks `secrets/`, `machines.yaml`,
  `*.env`, `*.pem`, `*.key`, `rclone.conf`, etc., and the loader hard-fails on any inline secret.
- **History:** keep the real inventory and run records versioned in the private companion
  repository. The public tool repository contains only generated examples.
