"""machines.yaml loading + schema validation + secret-reference resolution (pointers only) +
inline-secret scanning. The config file is INPUT and may be committed; it must never contain a
secret value, only a pointer (env var name / op:// path / absolute file path) to where the
secret lives. We validate that invariant here and refuse to run otherwise.
"""
from __future__ import annotations

import os
import re
import json
import posixpath
import shlex
from pathlib import Path

from . import AUTH_MODES

try:  # optional dependency; fall back to bundled subset parser
    import yaml as _pyyaml  # type: ignore

    class _InventoryLoader(_pyyaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError("mapping keys must be unique strings")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    def _yload(text):
        return _pyyaml.load(text, Loader=_InventoryLoader)
except ImportError:  # pragma: no cover - exercised on machines without PyYAML
    from . import yamlmin

    def _yload(text):
        return yamlmin.load(text)


class ConfigError(ValueError):
    pass


# Keys whose VALUE must never appear inline in machines.yaml.
_SECRET_VALUE_KEYS = ("refresh_token", "access_token", "client_secret", "private_key",
                      "rclone_config_pass")
# A pointer/placeholder looks like an env name, an op:// / vault path, an absolute path, or a
# clearly-empty/placeholder token. Anything else assigned to a secret key is a real secret.
_POINTER_RE = re.compile(r'^(?:[A-Z][A-Z0-9_]+|op://\S+|vault://\S+|aws-ssm://\S+|/\S+|<[^>]+>|""|\'\')?$')
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")
# long opaque blob that is not an obvious id; only flagged when assigned to a secretish key
_BLOB_RE = re.compile(r"[A-Za-z0-9+/=_-]{40,}")

_VALID_SOURCES = ("env", "file", "op", "vault", "aws-ssm")


class Config:
    def __init__(self, data: dict, path: str = "<memory>"):
        _validate_document(data)
        self.path = path
        # `version` is the machines.yaml schema_version; `schema_version` is an accepted alias.
        self.version = data.get("version", data.get("schema_version", 1))
        self.defaults = dict(data.get("defaults") or {})
        self.secrets = dict(data.get("secrets") or {})
        self.alerts = dict(data.get("alerts") or {})
        raw_hosts = data.get("hosts") or []
        self.hosts = []
        for h in raw_hosts:
            merged = dict(self.defaults)
            merged.update({k: v for k, v in (h or {}).items() if v is not None})
            self.hosts.append(merged)

    def auth_mode(self, host: dict) -> str:
        return host.get("auth_mode", self.defaults.get("auth_mode", "jwt"))


def _is_secretish(base: str) -> bool:
    """True if a (normalized) key name is one a secret value could plausibly hide under."""
    if any(base == k or base.endswith("_" + k) for k in _SECRET_VALUE_KEYS):
        return True
    return "secret" in base or "token" in base or "private" in base


def _scan_inline_secrets(text: str):
    """Return a list of (lineno, reason) for suspected inline secret VALUES."""
    hits = []
    # Track YAML block-scalar (`key: |` / `key: >`) state: a secret value can hide on the
    # indented continuation lines (no colon) that the per-line `key: value` logic would skip.
    block_indent = None      # column of the block-scalar KEY when inside a secret block
    for i, line in enumerate(text.splitlines(), 1):
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Inside a secret block scalar: scan the continuation content itself.
        if block_indent is not None:
            if indent > block_indent:
                if _PEM_RE.search(line) or _JWT_RE.search(line):
                    hits.append((i, "embedded key/JWT material in block scalar"))
                    continue
                content = stripped.split("#", 1)[0].strip().strip("\"'")
                if content and _BLOB_RE.fullmatch(content) and not _POINTER_RE.match(content):
                    hits.append((i, "opaque blob on secret block-scalar continuation"))
                continue
            block_indent = None  # dedent back to key level -> block ended; fall through
        if _PEM_RE.search(line) or _JWT_RE.search(line):
            hits.append((i, "embedded key/JWT material"))
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip().lower().lstrip("-").strip()
        val = val.strip()
        # strip an inline comment from the value (best-effort; quoted handled by parser elsewhere)
        if val and not (val[0] in "\"'"):
            val = val.split("#", 1)[0].strip()
        if val in ("", "''", '""'):
            continue
        # secret-bearing key with a value that is not a pointer/placeholder => real secret
        base = key.replace("-", "_")
        if any(base == k or base.endswith("_" + k) for k in _SECRET_VALUE_KEYS):
            v = val.strip("\"'")
            if not _POINTER_RE.match(v):
                hits.append((i, "inline value for secret key '%s'" % key))
                continue
        # generic high-entropy blob assigned to any *secret*-named key (not a *_ref pointer)
        if "secret" in base or "token" in base or "private" in base:
            if not base.endswith("_ref"):
                v = val.strip("\"'")
                if _BLOB_RE.fullmatch(v) and not _POINTER_RE.match(v):
                    hits.append((i, "opaque blob assigned to '%s'" % key))
        # A block scalar opened on a secret-bearing key: scan its continuation lines too. This
        # closes the gap where a literal secret hides under `<secret_key>: |` (incl. *_ref keys,
        # defense-in-depth) on indented lines that carry no colon.
        if _is_secretish(base) and val[:1] in ("|", ">"):
            block_indent = indent
    return hits


def load(path: str) -> Config:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except UnicodeError:
        raise ConfigError("configuration must be valid UTF-8") from None
    inline = _scan_inline_secrets(text)
    if inline:
        lines = ", ".join(str(number) for number, _ in inline)
        raise ConfigError("inline secret value(s) detected at line(s): " + lines)
    try:
        data = _yload(text)
    except Exception as exc:
        # Parser messages may contain the input, including a malformed literal secret.
        yaml_error = globals().get("_pyyaml")
        if isinstance(exc, (ValueError, TypeError, RecursionError)) or (
                yaml_error is not None and isinstance(exc, yaml_error.YAMLError)):
            raise ConfigError("invalid or unsupported configuration YAML") from None
        raise
    cfg = Config(data, path)
    validate(cfg)
    return cfg


def _validate_document(data):
    if not isinstance(data, dict):
        raise ConfigError("top-level YAML must be a mapping")
    for key in ("version", "schema_version"):
        if key in data and (type(data[key]) is not int or data[key] != 1):
            raise ConfigError("configuration schema version must be integer 1")
    for key in ("defaults", "secrets", "alerts"):
        if key in data and not isinstance(data[key], dict):
            raise ConfigError("configuration sections must be mappings")
    hosts = data.get("hosts", [])
    if not isinstance(hosts, list) or any(not isinstance(host, dict) for host in hosts):
        raise ConfigError("hosts must be a list of mappings")
    for host in [data.get("defaults", {}), *hosts]:
        if "secrets" in host and not isinstance(host["secrets"], dict):
            raise ConfigError("per-host secrets must be a mapping")
        _validate_host_fields(host)
    _validate_parsed_secrets(data, set(), data.get("secrets", {}).get("source", "env"))


def _validate_parsed_secrets(value, ancestors, source="env"):
    """Check parsed keys, including nested and quoted keys, without echoing values."""
    if not isinstance(value, (dict, list)):
        return
    identity = id(value)
    if identity in ancestors:
        raise ConfigError("recursive YAML structures are unsupported")
    ancestors.add(identity)
    try:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ConfigError("configuration mapping keys must be strings")
                normalized = key.lower().replace("-", "_")
                if normalized == "secrets" and not isinstance(child, dict):
                    raise ConfigError("secrets sections must be mappings of references")
                if normalized.endswith("_ref") and child is not None and child != "":
                    if not isinstance(child, str) or not (
                        _POINTER_RE.fullmatch(child) or (
                            source == "file" and Path(child).is_absolute()
                            and not any(ch.isspace() or ch == "\0" for ch in child)
                        )
                    ):
                        raise ConfigError("secret references must be pointers, not literal values")
                if normalized != "secrets" and not normalized.endswith("_ref") and _is_secretish(normalized):
                    if child is not None and child != "":
                        raise ConfigError("inline secret fields are forbidden; use secret references")
                child_source = child.get("source", source) if normalized == "secrets" else source
                _validate_parsed_secrets(child, ancestors, child_source)
        else:
            for child in value:
                _validate_parsed_secrets(child, ancestors, source)
    finally:
        ancestors.remove(identity)


def _validate_secret_refs(refs):
    if not isinstance(refs, dict):
        raise ConfigError("secrets must be a mapping")
    source = refs.get("source", "env")
    if not isinstance(source, str) or source not in _VALID_SOURCES:
        raise ConfigError("secrets.source is unsupported")
    for key, pointer in refs.items():
        if key == "source":
            continue
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*_ref", key):
            raise ConfigError("secrets may contain only source and reference fields")
        if pointer is None or pointer == "":
            continue
        if not isinstance(pointer, str):
            raise ConfigError("secret references must be strings")
        if re.fullmatch(r"<[^<>\r\n]+>", pointer):
            continue
        if source == "env":
            valid = re.fullmatch(r"[A-Z][A-Z0-9_]+", pointer)
        elif source == "file":
            valid = os.path.isabs(pointer) and not any(ch.isspace() or ch == "\0" for ch in pointer)
        else:
            valid = re.fullmatch(re.escape(source) + r"://[^\s\0]+", pointer)
        if not valid:
            raise ConfigError("secret reference does not match its source; literal values are forbidden")


def _merged_secret_refs(cfg, host, *, for_execution=False):
    overrides = host.get("secrets", {})
    if not isinstance(overrides, dict):
        raise ConfigError("per-host secrets must be a mapping")
    refs = dict(cfg.secrets)
    refs.update(overrides)
    if for_execution and refs.get('source', 'env') not in ('env', 'file'):
        raise ConfigError('execution supports env/file secrets; export other backends to one of these sources')
    _validate_secret_refs(refs)
    return refs


def validate(cfg: Config) -> None:
    if type(cfg.version) is not int or cfg.version != 1:
        raise ConfigError("configuration schema version must be integer 1")
    if not cfg.hosts:
        raise ConfigError("no hosts defined")
    _validate_secret_refs(cfg.secrets)
    for i, h in enumerate(cfg.hosts):
        validate_host(h)
        _merged_secret_refs(cfg, h)
        if not h.get("host"):
            raise ConfigError("hosts[%d] missing 'host'" % i)
        am = cfg.auth_mode(h)
        if am not in AUTH_MODES:
            raise ConfigError("hosts[%d] auth_mode %r not in %s" % (i, am, AUTH_MODES))
        if not h.get("remote_name"):
            raise ConfigError("hosts[%d] missing remote_name (set defaults.remote_name)" % i)
    names = [host['host'] for host in cfg.hosts]
    if len(names) != len(set(names)):
        raise ConfigError('host names must be unique')
    brokers = [host for host in cfg.hosts if cfg.auth_mode(host) == 'oauth-broker']
    if brokers and sum(host.get('broker_role') == 'master' for host in brokers) != 1:
        raise ConfigError('oauth-broker requires exactly one host with broker_role: master')


def validate_config_dir(path):
    """Require a dedicated Linux directory, including when validation runs on Windows."""
    if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in path.split('/'):
        raise ConfigError('config_dir must be an absolute Linux path without traversal or whitespace')
    # POSIX normalization preserves exactly two leading slashes; both roots are unsafe.
    if not posixpath.normpath(path).strip('/') or path.endswith('/'):
        raise ConfigError('config_dir must name a dedicated directory without a trailing slash')
    reserved = {'/opt/box-binder', '/opt/box-binder/boxbinder', '/opt/box-binder/install',
                '/usr/local/bin', '/etc/systemd/system'}
    for runtime_path in tuple(reserved):
        parent = posixpath.dirname(runtime_path)
        while parent != '/':
            reserved.add(parent)
            parent = posixpath.dirname(parent)
    reserved.update('/bin /boot /dev /home /lib /lib64 /media /mnt /proc /root /run /sbin '
                    '/srv /sys /tmp /var /var/cache /var/lib /var/log /var/run /var/spool '
                    '/var/tmp /usr/bin /usr/include /usr/lib /usr/lib64 /usr/sbin /usr/share '
                    '/usr/src /usr/local/etc /usr/local/lib /usr/local/share'.split())
    # Linux treats repeated leading separators as the same absolute path.
    if posixpath.normpath('/' + path.lstrip('/')) in reserved:
        raise ConfigError('config_dir must not be a shared system or runtime directory')


def _validate_host_fields(host):
    """Validate fields before defaults merge, including explicit nulls and overridden values."""
    if not isinstance(host, dict):
        raise ConfigError('host must be a mapping')
    string_fields = ('host', 'ssh', 'ssh_opts', 'auth_mode', 'box_sub_type', 'remote_name',
                     'root_folder_id', 'rclone_min_version', 'config_dir', 'health_interval',
                     'broker_role', 'impersonate_user_id')
    for key in string_fields:
        if key in host and not isinstance(host[key], str):
            raise ConfigError(key + ' must be a string')
    if 'remote_name' in host and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', host['remote_name']):
        raise ConfigError('remote_name must contain letters, digits or underscores')
    if host.get('box_sub_type', 'enterprise') not in ('enterprise', 'user'):
        raise ConfigError('box_sub_type must be enterprise or user')
    if not re.fullmatch(r'[0-9]+', host.get('root_folder_id', '0')):
        raise ConfigError('root_folder_id must be a nonempty numeric string')
    if not re.fullmatch(r'[0-9]*', host.get('impersonate_user_id', '')):
        raise ConfigError('impersonate_user_id must be empty or a numeric string')
    if 'rclone_min_version' in host and not re.fullmatch(
            r'v?[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)*', host['rclone_min_version']):
        raise ConfigError('rclone_min_version must be a version string')
    options = host.get('ssh_opts', '')
    if any(character in options for character in '\n\r\0'):
        raise ConfigError('ssh_opts must not contain line breaks or NUL')
    try:
        shlex.split(options)
    except ValueError:
        raise ConfigError('ssh_opts must contain balanced shell quoting') from None
    validate_config_dir(host.get('config_dir', '/etc/box-binder'))
    if not re.fullmatch(r'[A-Za-z0-9*/:,. -]+', host.get('health_interval', '*:0/15')):
        raise ConfigError('health_interval contains unsupported characters')
    value = host.get('mint_interval_min', 45)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value < 60:
        raise ConfigError('mint_interval_min must be an integer from 1 to 59')
    if host.get('auth_mode', 'jwt') not in AUTH_MODES:
        raise ConfigError('unsupported auth_mode')
    if host.get('broker_role', 'slave') not in ('master', 'slave'):
        raise ConfigError('broker_role must be master or slave')
    for key in ('host', 'ssh'):
        if key not in host:
            continue
        value = host.get(key, '')
        if (not isinstance(value, str) or value.startswith('-')
                or any(character in value for character in '\n\r\0')
                or (key == 'host' and (not value or value != value.strip()))):
            raise ConfigError('invalid host or SSH target')


def validate_host(host):
    _validate_host_fields(host)
    if not host.get('host'):
        raise ConfigError('host is required')
    if not host.get('remote_name'):
        raise ConfigError('remote_name is required')


def secret_values(cfg, host):
    """Resolve supported sources for transport only; callers must never report this result."""
    mode = cfg.auth_mode(host)
    if mode == 'oauth-broker' and host.get('broker_role') != 'master':
        return {}
    refs = _merged_secret_refs(cfg, host, for_execution=True)
    source = refs.get('source', 'env')
    required = ['jwt_config'] if mode == 'jwt' else ['client_id', 'client_secret']
    if mode in ('ccg-native', 'ccg-mint'):
        required.append('box_subject_id')
    if mode == 'oauth-broker' and refs.get('broker_state_ref'):
        required.append('broker_state')
    result = {}
    for key in required:
        pointer = refs.get(key + '_ref')
        try:
            if not isinstance(pointer, str) or not pointer:
                raise ValueError
            value = os.environ.get(pointer) if source == 'env' else (
                Path(pointer).read_text(encoding='utf-8') if os.path.isabs(pointer) else None)
            if not isinstance(value, str) or not value.strip():
                raise ValueError
            if key in ('jwt_config', 'broker_state'):
                parsed = json.loads(value)
                if not isinstance(parsed, dict) or not parsed:
                    raise ValueError
                if key == 'broker_state' and not isinstance(parsed.get('refresh_token'), str):
                    raise ValueError
            result[key] = value
        except (OSError, ValueError, TypeError):
            raise ConfigError('required secret reference unavailable or invalid: ' + key + '_ref') from None
    return result


def resolve_refs(cfg: Config) -> dict:
    """Resolve secret *references* to a presence map WITHOUT reading any value.

    Returns {ref_name: {"source", "present": bool, "detail"}}. For source=env we test
    os.environ membership; for source=file we test absolute-path + existence; for backend
    sources we validate the pointer format. The secret VALUE is never read or returned.
    """
    src = cfg.secrets.get("source", "env")
    out = {}
    for k, v in cfg.secrets.items():
        if not k.endswith("_ref") or v is None:
            continue
        ref = str(v)
        entry = {"source": src, "present": False, "detail": ""}
        if src == "env":
            entry["present"] = ref in os.environ
            entry["detail"] = "env var %s" % ref
        elif src == "file":
            entry["present"] = os.path.isabs(ref) and os.path.exists(ref)
            entry["detail"] = "file %s" % ref
        else:  # op / vault / aws-ssm: validate pointer shape only
            entry["present"] = bool(re.match(r"^(op|vault|aws-ssm)://\S+$", ref))
            entry["detail"] = "%s pointer" % src
        out[k] = entry
    return out


# ---- anti-pattern guard (signal 8) -------------------------------------------------------

class AntiPatternError(RuntimeError):
    pass


def assert_no_shared_refresh_token(cfg: Config, rclone_conf_text: str = "") -> None:
    """Reject the cardinal Box multi-host failure mode.

    A rotating OAuth refresh_token must NEVER be deployed to more than one host (any host that
    refreshes invalidates the others). Server-auth modes (jwt/ccg-*) carry no refresh_token.
    In oauth-broker mode exactly one host (the master) may hold it.
    """
    has_rt = bool(rclone_conf_text) and "refresh_token" in rclone_conf_text
    if not has_rt:
        return
    n = len(cfg.hosts)
    modes = {cfg.auth_mode(h) for h in cfg.hosts}
    if n > 1 and modes != {"oauth-broker"}:
        raise AntiPatternError(
            "refresh_token present with %d hosts in mode(s) %s: rotating refresh tokens cannot "
            "be shared across hosts. Use server auth (jwt/ccg) or oauth-broker (single master)."
            % (n, sorted(modes))
        )
