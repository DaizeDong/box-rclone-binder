# box-rclone-binder, Config (repo overview)

The tool reads a machine inventory, `machines.yaml`, containing hosts,
authentication modes and secret references. The authoritative
[configuration contract](skills/box-rclone-binder/CONFIG.md) defines every field,
type, default, discovery step, secret backend and retained-profile switch.

## Local configuration checks

Select a PRIVATE companion containing exactly `machines.yaml` with
`BOX_RCLONE_BINDER_CONFIG` or `BOX_RCLONE_BINDER_CONFIG_DIR`.
The full contract also describes explicit CLI selection and shared discovery
through `guards/tools/datadir.py`.
The root scripts below delegate to `skills/box-rclone-binder/scripts/`:

- `python scripts/init_config.py` writes the generated `machines.example.yaml`
  template byte for byte to the selected inventory.
- `python scripts/verify_config.py` prints the resolved path, validates schema and
  checks required references. Missing required references report NOT READY and
  exit 3; this local check does not probe a remote host.

This tool is retired. Keep inventories and run records only for an active
deployment or retained recovery need, as defined in [DATA.md](DATA.md).
Real secret values and `secrets/` remain outside the public tool; inventories
contain `*_ref` pointers and the loader rejects inline values.
