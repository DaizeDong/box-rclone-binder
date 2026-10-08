# Private configuration and retirement

This tool is in maintenance-only status. Preserve source needed to understand or
recover an existing deployment; installing or retiring a local copy does not
authorize changes to remote mounts, services or accounts.

[storage.contract.json](storage.contract.json) declares minimal private retention.
An existing verified PRIVATE companion can contain a `.companion` file with the
single line `box-rclone-binder` when literal Git configuration cannot establish
discovery. This identity does not initialize a deployment or replace write checks.
[The configuration schema](skills/box-rclone-binder/CONFIG.md) remains authoritative
for `machines.yaml`, host validation and secret references. Retained profiles use separate
PRIVATE companions, each with that exact filename. Keep an inventory only
for an active deployment or an explicitly retained recovery need. Do not create an
empty inventory to make a retired tool appear configured.

An existing PRIVATE companion may retain `data/box-oauth.env` as selected recovery
material. Keep credential bytes private and restore them through the approved backup.
File presence does not prove a token is valid, a remote deployment is active, or
recovery has been tested. Retire this material only when recovery is no longer
required and credential rotation or revocation has been reconciled.

The companion needs only current recovery material, necessary exclusions and a short
README linking here. No second archive or copied development history is required.
Use skill-smith's shared `storage_contract.py` for contract validation and metadata
inventory; it does not execute the domain schema or contact a remote host.
