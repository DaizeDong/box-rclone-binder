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
  then `$BOX_RCLONE_BINDER_CONFIG_DIR`, then `./machines.yaml`, then
  `~/.box-rclone-binder-config/machines.yaml`, then `~/.config/box-rclone-binder/machines.yaml`.
- **First-time (deterministic stamp):** `python scripts/init_config.py --out <dir>` writes a
  `machines.yaml` byte-identical to the committed `machines.example.yaml` template.
- **Verify / hot-swap:** `python scripts/verify_config.py` resolves + validates the config the env
  var points at and prints the resolved path, so switching `$BOX_RCLONE_BINDER_CONFIG` between two
  configs is provable. (Root `scripts/init_config.py` and `scripts/verify_config.py` are thin shims
  delegating to `skills/box-rclone-binder/scripts/`.)
- **Secrets, Mode B:** the live `machines.yaml` carries only `*_ref` pointers; real values live in
  your backend (`env`/`file`/`op`/`vault`/`aws-ssm`). `.gitignore` blocks `secrets/`, `machines.yaml`,
  `*.env`, `*.pem`, `*.key`, `rclone.conf`, etc., and the loader hard-fails on any inline secret.
