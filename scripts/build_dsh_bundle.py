from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

import shadow_sdk.plugin_contracts as plugin_contracts_module
from shadow_sdk.plugin_contracts import (
    PluginContractError,
    ValidatedPlugin,
    contract_schema_path,
    dsh_tool_name,
    load_document,
    semver_satisfies,
    sha256_file,
    validate_document,
    validate_plugin,
)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MAX_SKILL_RESOURCE_BYTES = 10 * 1024 * 1024
MAX_SKILL_DIRECTORY_BYTES = 50 * 1024 * 1024
SENSITIVE_SKILL_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".keystore"}


def _resolve_ref(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return deepcopy(value)
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise PluginContractError(f"only local OpenAPI refs are supported: {ref}")
    current: Any = document
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise PluginContractError(f"unresolved OpenAPI ref: {ref}")
        current = current[key]
    resolved = deepcopy(current)
    siblings = {key: deepcopy(item) for key, item in value.items() if key != "$ref"}
    if not siblings:
        return resolved
    if not isinstance(resolved, dict):
        raise PluginContractError(f"OpenAPI ref with siblings must resolve to an object: {ref}")
    resolved.update(siblings)
    return resolved


def _dsh_value_spec(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    schema = _resolve_ref(document, schema)
    if not isinstance(schema, dict) or not schema:
        return {"type": "json"}
    union = schema.get("oneOf", schema.get("anyOf"))
    if union is not None:
        branches = [_dsh_value_spec(document, item) for item in union]
        if len(branches) < 2:
            raise PluginContractError("OpenAPI oneOf/anyOf requires at least two branches")
        return {"oneOf": branches}
    schema_type = schema.get("type")
    if schema_type not in {"string", "number", "integer", "boolean", "null", "array", "object"}:
        return {"type": "json"}
    result: dict[str, Any] = {"type": schema_type}
    for annotation in ("description", "title", "default", "examples", "enum", "const"):
        if annotation in schema:
            result[annotation] = deepcopy(schema[annotation])
    if schema_type == "array" and "items" in schema:
        result["items"] = _dsh_value_spec(document, schema["items"])
    if schema_type == "object":
        required = set(schema.get("required", []))
        properties: dict[str, Any] = {}
        for name, child in schema.get("properties", {}).items():
            child_spec = _dsh_value_spec(document, child)
            if name in required:
                child_spec["required"] = True
            properties[name] = child_spec
        if properties:
            result["properties"] = properties
        result["additionalProperties"] = schema.get("additionalProperties", True) is not False
    if schema.get("nullable") is True and schema_type != "null":
        return {"oneOf": [result, {"type": "null"}]}
    return result


def _find_operation(document: dict[str, Any], operation_id: str) -> tuple[str, str, dict[str, Any]]:
    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict) and operation.get("operationId") == operation_id:
                merged = deepcopy(operation)
                merged["parameters"] = [
                    *deepcopy(path_item.get("parameters", [])),
                    *deepcopy(operation.get("parameters", [])),
                ]
                return method.upper(), path, merged
    raise PluginContractError(f"OpenAPI operationId not found: {operation_id}")


def _response_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses", {})
    for status in sorted(responses, key=str):
        if not (str(status).startswith("2") or status == "default"):
            continue
        response = _resolve_ref(document, responses[status])
        content = response.get("content", {}) if isinstance(response, dict) else {}
        media = content.get("application/json", {})
        if isinstance(media, dict) and "schema" in media:
            return _dsh_value_spec(document, media["schema"])
    return {"type": "object", "additionalProperties": True}


def _compile_http_tool(
    plugin: ValidatedPlugin, capability: dict[str, Any], tool: dict[str, Any]
) -> dict[str, Any]:
    contract_path = plugin.root / tool["contract_ref"]
    document = load_document(contract_path)
    method, path, operation = _find_operation(document, tool["operation_id"])
    parameters: dict[str, Any] = {}
    path_parameters: list[str] = []
    query_parameters: list[dict[str, Any]] = []
    for raw_parameter in operation.get("parameters", []):
        parameter = _resolve_ref(document, raw_parameter)
        location = parameter.get("in")
        if location not in {"path", "query"}:
            raise PluginContractError(
                f"{tool['name']} uses unsupported OpenAPI parameter location: {location}"
            )
        name = parameter.get("name")
        if not isinstance(name, str) or not name:
            raise PluginContractError(f"{tool['name']} contains a parameter without a name")
        spec = _dsh_value_spec(document, parameter.get("schema", {}))
        if parameter.get("required") is True or parameter.get("in") == "path":
            spec["required"] = True
        if name in parameters:
            raise PluginContractError(f"{tool['name']} contains duplicate parameter {name}")
        parameters[name] = spec
        if location == "path":
            if spec.get("type") in {"array", "object", "json"}:
                raise PluginContractError(
                    f"{tool['name']} path parameter {name} must be scalar"
                )
            path_parameters.append(name)
        else:
            if parameter.get("style", "form") != "form":
                raise PluginContractError(
                    f"{tool['name']} query parameter {name} must use form style"
                )
            if spec.get("type") == "object":
                raise PluginContractError(
                    f"{tool['name']} query parameter {name} cannot be an object"
                )
            query_parameters.append(
                {
                    "name": name,
                    "array": spec.get("type") == "array",
                    "explode": parameter.get("explode", True) is not False,
                }
            )

    has_body = False
    body = operation.get("requestBody")
    if body is not None:
        body = _resolve_ref(document, body)
        media = body.get("content", {}).get("application/json", {})
        if "schema" not in media:
            raise PluginContractError(f"{tool['name']} requestBody must declare application/json")
        body_spec = _dsh_value_spec(document, media["schema"])
        if body.get("required") is True:
            body_spec["required"] = True
        parameters["body"] = body_spec
        has_body = True

    return {
        "name": dsh_tool_name(tool["name"]),
        "shadowName": tool["name"],
        "capabilityId": capability["id"],
        "description": capability["summary"],
        "parameters": parameters,
        "output": _response_schema(document, operation),
        "method": method,
        "path": path,
        "pathParameters": path_parameters,
        "queryParameters": query_parameters,
        "hasBody": has_body,
        "timeoutMs": tool["timeout_ms"],
        "concurrencySafe": tool["concurrency_safe"],
        "resultMode": tool["result_mode"],
        "maxResultBytes": tool["max_result_bytes"],
        "maxModelChars": tool["max_model_chars"],
        "exposure": tool["exposure"],
        "riskLevel": capability["risk_level"],
        "effect": capability["effect"],
        "idempotencyRequired": capability["idempotency_required"],
        "retryPolicy": tool["retry_policy"],
    }


def _selected_capabilities(plugin: ValidatedPlugin, selected: Any) -> list[dict[str, Any]]:
    capabilities = plugin.agent_manifest["capabilities"]
    if selected == "*":
        return capabilities
    selected_ids = set(selected)
    known = {item["id"] for item in capabilities}
    unknown = sorted(selected_ids - known)
    if unknown:
        raise PluginContractError(
            f"{plugin.plugin_id} profile selects unknown capabilities: {unknown}"
        )
    return [item for item in capabilities if item["id"] in selected_ids]


def _compile_plugin(
    plugin: ValidatedPlugin, instance_id: str, instance: dict[str, Any], selected: Any
) -> dict[str, Any]:
    capabilities = _selected_capabilities(plugin, selected)
    selected_ids = {item["id"] for item in capabilities}
    tools: list[dict[str, Any]] = []
    for capability in capabilities:
        for tool in capability["tools"]:
            if tool["exposure"] == "hidden":
                continue
            if tool["transport"] != "http":
                raise PluginContractError(
                    f"{tool['name']}: the initial DSH builder supports HTTP/OpenAPI; "
                    "MCP adapter pending"
                )
            tools.append(_compile_http_tool(plugin, capability, tool))

    skills = []
    agent_dir = plugin.descriptor_paths["agent"].parent
    for skill in plugin.agent_manifest["skills"]:
        if not selected_ids.intersection(skill["capabilities"]):
            continue
        invocation = skill.get("invocation", {"model": True, "user": True})
        skills.append(
            {
                "name": skill["id"],
                "description": skill["summary"],
                "content": (agent_dir / skill["path"]).read_text(encoding="utf-8"),
                "invocation": {
                    "modelInvocable": invocation["model"],
                    "userInvocable": invocation["user"],
                },
                "resourcePath": f"skills/{plugin.plugin_id}/{skill['id']}",
            }
        )
    return {
        "pluginId": plugin.plugin_id,
        "pluginVersion": plugin.version,
        "instanceId": instance_id,
        "baseUrlEnv": instance["base_url_env"],
        "credentialEnv": instance["credential_env"],
        "tools": tools,
        "skills": skills,
    }


def _validate_dsh_compatibility(plugin: ValidatedPlugin, runtime: dict[str, Any]) -> None:
    compatibility = plugin.definition["spec"]["compatibility"].get("dsh")
    if not isinstance(compatibility, dict):
        raise PluginContractError(f"{plugin.plugin_id} does not declare DSH compatibility")
    checks = (
        ("distribution", "distribution_version"),
        ("tools_api", "tools_api_version"),
    )
    for requirement_key, version_key in checks:
        requirement = compatibility[requirement_key]
        version = runtime[version_key]
        if not semver_satisfies(version, requirement):
            raise PluginContractError(
                f"{plugin.plugin_id} requires DSH {requirement_key} {requirement}; "
                f"profile pins {version}"
            )


def _skill_resource_files(skill_file: Path) -> list[Path]:
    resource_root = skill_file.parent.resolve()
    files: list[Path] = []
    total_bytes = 0
    for candidate in sorted(resource_root.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise PluginContractError(f"skill resources cannot contain symlinks: {candidate}")
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(resource_root):
            raise PluginContractError(f"skill resource escapes its directory: {candidate}")
        if candidate.name.startswith(".") or candidate.suffix.lower() in SENSITIVE_SKILL_SUFFIXES:
            raise PluginContractError(f"skill resources contain a forbidden file: {candidate}")
        size = candidate.stat().st_size
        if size > MAX_SKILL_RESOURCE_BYTES:
            raise PluginContractError(f"skill resource exceeds 10 MiB: {candidate}")
        total_bytes += size
        if total_bytes > MAX_SKILL_DIRECTORY_BYTES:
            raise PluginContractError(f"skill directory exceeds 50 MiB: {resource_root}")
        files.append(resolved)
    return files


DOMAIN_JS = """import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { PROFILE } from './profile.generated.js'
import { executeHttp, renderValue } from './runtime.js'

export const name = 'shadow-domain'
export const inject = ['tools', 'skills']

export function apply(ctx, config) {
  const domain = PROFILE.domains.find((item) => item.instanceId === config.instanceId)
  if (!domain) throw new Error(`unknown generated Shadow instance: ${config.instanceId}`)
  for (const skill of domain.skills) {
    const { resourcePath, ...definition } = skill
    ctx.skills.register({
      ...definition,
      source: 'runtime',
      provider: `shadow:${domain.pluginId}`,
      resourceBase: {
        kind: 'directory',
        path: fileURLToPath(new URL(`./${resourcePath}/`, import.meta.url)),
      },
    })
  }
  for (const tool of domain.tools) {
    ctx.tools.register(defineTool({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
      output: {
        schema: tool.output,
        render: (_args, value) => renderValue(value, tool),
      },
      timeoutMs: tool.timeoutMs,
      isConcurrencySafe: () => tool.concurrencySafe === true,
      execute: (args, exec) => executeHttp(domain, tool, args, exec),
    }))
  }
}
"""


POLICY_JS = """import { PROFILE } from './profile.generated.js'

export const name = 'shadow-policy'
export const inject = ['tools']

const policies = new Map(
  PROFILE.domains.flatMap((domain) => domain.tools.map((tool) => [tool.name, tool])),
)

function stricter(decision, required) {
  if (decision?.kind === 'deny' || decision?.kind === 'ask') return decision
  return required
}

export function apply(ctx) {
  ctx.on('tools/pre-execute', async (exec, next) => {
    const tool = policies.get(exec.name)
    if (!tool) return next()
    if (tool.riskLevel === 'L0' || tool.riskLevel === 'L1') return next()
    if (
      tool.riskLevel === 'L2'
      && PROFILE.policy.preauthorizedCapabilities.includes(tool.capabilityId)
    ) {
      return next()
    }
    if (tool.riskLevel === 'L4' && !PROFILE.policy.allowElevated) {
      return { kind: 'deny', reason: 'This Shadow profile does not allow elevated capabilities.' }
    }
    const downstream = await next()
    return stricter(downstream, {
      kind: 'ask',
      reason: `Shadow ${tool.riskLevel} capability requires approval.`,
    })
  })
  ctx.tools.guard((exec) => {
    const tool = policies.get(exec.name)
    if (tool?.riskLevel === 'L4' && !PROFILE.policy.allowElevated) {
      return 'Elevated Shadow capability is disabled for this profile.'
    }
    return undefined
  })
}
"""


RUNTIME_JS = r"""import { createHash } from 'node:crypto'

function requiredEnv(name) {
  const value = process.env[name]
  if (!value) throw new Error(`required environment variable is missing: ${name}`)
  return value
}

function boundedJson(value, maxBytes) {
  const encoded = JSON.stringify(value)
  if (Buffer.byteLength(encoded, 'utf8') > maxBytes) {
    throw new Error(`domain result exceeds the declared ${maxBytes}-byte boundary`)
  }
  return encoded
}

function safeRequestId(callId) {
  return createHash('sha256').update(`shadow-dsh:${callId}`).digest('hex').slice(0, 32)
}

async function readBoundedJson(response, maxBytes) {
  const contentType = response.headers.get('content-type') ?? ''
  if (!/(?:application\/json|\+json)(?:\s*;|$)/iu.test(contentType)) {
    throw new Error('domain response is not JSON')
  }
  const declaredLength = Number(response.headers.get('content-length'))
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new Error(`domain result exceeds the declared ${maxBytes}-byte boundary`)
  }
  if (!response.body) throw new Error('domain response body is missing')
  const reader = response.body.getReader()
  const chunks = []
  let total = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    total += value.byteLength
    if (total > maxBytes) {
      await reader.cancel('Shadow result boundary exceeded')
      throw new Error(`domain result exceeds the declared ${maxBytes}-byte boundary`)
    }
    chunks.push(value)
  }
  const bytes = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch {
    throw new Error('domain response contains invalid JSON')
  }
}

export function renderValue(value, tool) {
  let modelValue = value
  if (tool.resultMode === 'summary') {
    modelValue = { summary: value.summary }
    if (typeof value.resource_uri === 'string') modelValue.resource_uri = value.resource_uri
    if (value.continuation !== undefined) modelValue.continuation = value.continuation
  } else if (tool.resultMode === 'reference') {
    modelValue = { resource_uri: value.resource_uri }
    if (typeof value.summary === 'string') modelValue.summary = value.summary
    if (value.continuation !== undefined) modelValue.continuation = value.continuation
  }
  const rendered = boundedJson(modelValue, tool.maxResultBytes)
  if (rendered.length > tool.maxModelChars) {
    throw new Error(
      `model result exceeds the declared ${tool.maxModelChars}-character boundary`,
    )
  }
  return [{ type: 'text', text: rendered }]
}

export async function executeHttp(domain, tool, args, exec) {
  const configuredBaseUrl = requiredEnv(domain.baseUrlEnv)
  let parsedBaseUrl
  try {
    parsedBaseUrl = new URL(configuredBaseUrl)
  } catch {
    throw new Error('domain base URL is invalid')
  }
  if (!['http:', 'https:'].includes(parsedBaseUrl.protocol)) {
    throw new Error('domain base URL must use HTTP or HTTPS')
  }
  if (
    parsedBaseUrl.username
    || parsedBaseUrl.password
    || parsedBaseUrl.search
    || parsedBaseUrl.hash
  ) {
    throw new Error('domain base URL cannot contain credentials, query, or fragment')
  }
  const serializedBaseUrl = parsedBaseUrl.toString()
  const baseUrl = serializedBaseUrl.endsWith('/')
    ? serializedBaseUrl.slice(0, -1)
    : serializedBaseUrl
  let path = tool.path
  for (const name of tool.pathParameters) {
    path = path.replace(`{${name}}`, encodeURIComponent(String(args[name])))
  }
  const url = new URL(`${baseUrl}${path}`)
  for (const parameter of tool.queryParameters) {
    const value = args[parameter.name]
    if (value !== undefined && value !== null) {
      if (parameter.array && Array.isArray(value)) {
        if (parameter.explode) {
          for (const item of value) url.searchParams.append(parameter.name, String(item))
        } else {
          url.searchParams.set(parameter.name, value.map(String).join(','))
        }
      } else {
        url.searchParams.set(parameter.name, String(value))
      }
    }
  }
  const callId = String(exec.callId ?? 'unknown')
  const requestId = safeRequestId(callId)
  const headers = {
    Accept: 'application/json',
    Authorization: `Bearer ${requiredEnv(domain.credentialEnv)}`,
    'X-Request-ID': `shadow-dsh-${requestId}`,
  }
  if (tool.hasBody) headers['Content-Type'] = 'application/json'
  if (tool.idempotencyRequired) headers['Idempotency-Key'] = `shadow-dsh-${requestId}`
  const attempts = tool.retryPolicy === 'idempotent' ? 2 : 1
  let response
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      response = await fetch(url, {
        method: tool.method,
        headers,
        body: tool.hasBody ? JSON.stringify(args.body) : undefined,
        signal: exec.signal,
      })
    } catch {
      if (exec.signal.aborted) throw new Error('domain request was cancelled')
      if (attempt + 1 >= attempts) throw new Error('domain request failed')
      continue
    }
    if (![502, 503, 504].includes(response.status) || attempt + 1 >= attempts) break
    await response.body?.cancel()
  }
  if (!response?.ok) throw new Error(`domain request failed with HTTP ${response?.status ?? 503}`)
  const value = await readBoundedJson(response, tool.maxResultBytes)
  boundedJson(value, tool.maxResultBytes)
  if (tool.resultMode === 'reference' && (!value || typeof value.resource_uri !== 'string')) {
    throw new Error('reference-mode result must contain resource_uri')
  }
  if (tool.resultMode === 'summary' && (!value || typeof value.summary !== 'string')) {
    throw new Error('summary-mode result must contain summary')
  }
  return value
}
"""


def build_bundle(
    *,
    platform_root: Path,
    profile_path: Path,
    instances_path: Path,
    plugin_roots: list[Path],
    output_dir: Path,
) -> Path:
    profile = load_document(profile_path)
    instances_document = load_document(instances_path)
    validate_document(
        profile, contract_schema_path(platform_root, "agent-profile.schema.json"), label="profile"
    )
    validate_document(
        instances_document,
        contract_schema_path(platform_root, "shadow-plugin-instance.schema.json"),
        label="plugin instances",
    )
    validated_plugins = [validate_plugin(root, platform_root) for root in plugin_roots]
    plugins = {item.plugin_id: item for item in validated_plugins}
    if len(plugins) != len(plugin_roots):
        raise PluginContractError("duplicate plugin ids in build inputs")

    selected_plugin_ids = [item["plugin_id"] for item in profile["plugins"]]
    selected_instance_ids = [item["instance_id"] for item in profile["plugins"]]
    if len(selected_plugin_ids) != len(set(selected_plugin_ids)):
        raise PluginContractError("a profile cannot select the same plugin more than once")
    if len(selected_instance_ids) != len(set(selected_instance_ids)):
        raise PluginContractError("a profile cannot select the same instance more than once")

    compiled_domains: list[dict[str, Any]] = []
    input_labels: dict[Path, str] = {
        profile_path.resolve(): f"profile/{profile_path.name}",
        instances_path.resolve(): f"instances/{instances_path.name}",
        Path(__file__).resolve(): "builder/scripts/build_dsh_bundle.py",
        Path(plugin_contracts_module.__file__).resolve(): (
            "builder/shadow_sdk/plugin_contracts.py"
        ),
    }
    for schema_name in (
        "agent-capability-manifest.schema.json",
        "agent-profile.schema.json",
        "shadow-plugin-instance.schema.json",
        "shadow-plugin.schema.json",
        "shadow-tool-result.schema.json",
        "confirmation-receipt.schema.json",
    ):
        schema_path = contract_schema_path(platform_root, schema_name).resolve()
        input_labels[schema_path] = f"builder/contracts/{schema_name}"
    skill_directories: list[tuple[Path, Path]] = []
    for selected in profile["plugins"]:
        plugin_id = selected["plugin_id"]
        plugin = plugins.get(plugin_id)
        if plugin is None:
            raise PluginContractError(f"profile references missing plugin: {plugin_id}")
        _validate_dsh_compatibility(plugin, profile["runtime"])
        instance_id = selected["instance_id"]
        instance = instances_document["instances"].get(instance_id)
        if instance is None:
            raise PluginContractError(f"profile references missing instance: {instance_id}")
        if not instance["enabled"]:
            raise PluginContractError(f"profile references disabled instance: {instance_id}")
        if instance["plugin_id"] != plugin_id or instance["plugin_version"] != plugin.version:
            raise PluginContractError(
                f"instance {instance_id} does not match {plugin_id}@{plugin.version}"
            )
        compiled_domain = _compile_plugin(
            plugin, instance_id, instance, selected["capabilities"]
        )
        compiled_domains.append(compiled_domain)
        input_labels[(plugin.root / "shadow-plugin.yaml").resolve()] = (
            f"plugins/{plugin_id}/shadow-plugin.yaml"
        )
        for descriptor_path in plugin.descriptor_paths.values():
            relative = descriptor_path.resolve().relative_to(plugin.root.resolve()).as_posix()
            input_labels[descriptor_path.resolve()] = f"plugins/{plugin_id}/{relative}"
        agent_dir = plugin.descriptor_paths["agent"].parent
        selected_skill_ids = {skill["name"] for skill in compiled_domain["skills"]}
        for skill in plugin.agent_manifest["skills"]:
            if skill["id"] not in selected_skill_ids:
                continue
            skill_file = (agent_dir / skill["path"]).resolve()
            source_dir = skill_file.parent
            destination = Path("skills") / plugin_id / skill["id"]
            skill_directories.append((source_dir, destination))
            for resource in _skill_resource_files(skill_file):
                relative = resource.relative_to(plugin.root.resolve()).as_posix()
                input_labels[resource] = f"plugins/{plugin_id}/{relative}"
        for capability in plugin.agent_manifest["capabilities"]:
            for tool in capability["tools"]:
                contract_path = (plugin.root / tool["contract_ref"]).resolve()
                relative = contract_path.relative_to(plugin.root.resolve()).as_posix()
                input_labels[contract_path] = f"plugins/{plugin_id}/{relative}"

    tool_names = [tool["name"] for domain in compiled_domains for tool in domain["tools"]]
    if len(tool_names) != len(set(tool_names)):
        raise PluginContractError("DSH tool name collision across selected plugins")
    skill_names = [skill["name"] for domain in compiled_domains for skill in domain["skills"]]
    if len(skill_names) != len(set(skill_names)):
        raise PluginContractError("DSH skill name collision across selected plugins")

    tool_catalog_chars = sum(
        len(
            json.dumps(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                    "output": tool["output"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        for domain in compiled_domains
        for tool in domain["tools"]
    )
    skill_catalog_chars = sum(
        len(skill["name"]) + len(skill["description"])
        for domain in compiled_domains
        for skill in domain["skills"]
    )
    if tool_catalog_chars > profile["budgets"]["max_tool_catalog_chars"]:
        raise PluginContractError(
            "selected tools exceed the profile max_tool_catalog_chars budget: "
            f"{tool_catalog_chars}"
        )
    if skill_catalog_chars > profile["budgets"]["max_skill_catalog_chars"]:
        raise PluginContractError(
            "selected skills exceed the profile max_skill_catalog_chars budget: "
            f"{skill_catalog_chars}"
        )

    input_records = [
        {"path": label, "sha256": sha256_file(path)}
        for path, label in sorted(input_labels.items(), key=lambda item: item[1])
    ]
    build_id = hashlib.sha256(
        json.dumps(input_records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    package_name = f"dsh-{profile['id']}"
    package_version = f"0.0.0-shadow.{build_id[:12]}"
    target = output_dir.resolve() / package_name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for source, destination in skill_directories:
        shutil.copytree(source, target / destination)
    generated_profile = {
        "profileId": profile["id"],
        "runtime": {
            "kind": profile["runtime"]["kind"],
            "distributionVersion": profile["runtime"]["distribution_version"],
            "toolsApiVersion": profile["runtime"]["tools_api_version"],
        },
        "policy": {
            "preauthorizedCapabilities": profile["policy"]["preauthorized_capabilities"],
            "allowElevated": profile["policy"]["allow_elevated"],
        },
        "budgets": {
            "toolCatalogChars": tool_catalog_chars,
            "skillCatalogChars": skill_catalog_chars,
        },
        "domains": compiled_domains,
    }
    package = {
        "name": package_name,
        "version": package_version,
        "description": f"Generated Shadow Agent bundle for profile {profile['id']}",
        "private": True,
        "type": "module",
        "engines": {"node": "^22.19.0 || >=24.0.0"},
        "keywords": ["dsh-plugin", "deepseek-harness", "shadow-agent"],
        "files": [
            "agent-bundle.lock",
            "cordis.patch.yml",
            "domain.js",
            "policy.js",
            "profile.generated.js",
            "runtime.js",
            "shadow-runtime-manifest.json",
            "skills",
        ],
        "exports": {"./domain": "./domain.js", "./policy": "./policy.js"},
        "peerDependencies": {
            "@deepseek-ai/dsh-tools": profile["runtime"]["tools_api_version"]
        },
        "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
    }
    patch = [
        {
            "insert": [
                {"id": "shadow-policy", "name": f"{package_name}/policy"},
                *[
                    {
                        "id": f"shadow-domain-{domain['pluginId'].removeprefix('shadow-')}",
                        "name": f"{package_name}/domain",
                        "config": {"instanceId": domain["instanceId"]},
                    }
                    for domain in compiled_domains
                ],
            ]
        }
    ]
    lock = {
        "version": 3,
        "build_id": build_id,
        "package_name": package_name,
        "package_version": package_version,
        "profile_id": profile["id"],
        "runtime": "dsh",
        "runtime_distribution_version": profile["runtime"]["distribution_version"],
        "runtime_tools_api_version": profile["runtime"]["tools_api_version"],
        "plugins": [
            {
                "plugin_id": domain["pluginId"],
                "plugin_version": domain["pluginVersion"],
                "instance_id": domain["instanceId"],
                "dsh_compatibility": plugins[domain["pluginId"]]
                .definition["spec"]["compatibility"]["dsh"],
            }
            for domain in compiled_domains
        ],
        "model_exposure": {
            "tool_catalog_chars": tool_catalog_chars,
            "skill_catalog_chars": skill_catalog_chars,
        },
        "inputs": input_records,
        "source_date_epoch": int(os.environ.get("SOURCE_DATE_EPOCH", "0")),
    }
    (target / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "cordis.patch.yml").write_text(
        yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (target / "profile.generated.js").write_text(
        "export const PROFILE = "
        + json.dumps(generated_profile, ensure_ascii=False, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    (target / "domain.js").write_text(DOMAIN_JS, encoding="utf-8")
    (target / "policy.js").write_text(POLICY_JS, encoding="utf-8")
    (target / "runtime.js").write_text(RUNTIME_JS, encoding="utf-8")
    runtime_manifest = {
        "version": 1,
        "adapter": "shadow-dsh",
        "profile_id": profile["id"],
        "build_id": build_id,
        "runtime": generated_profile["runtime"],
        "model_exposure": lock["model_exposure"],
        "domains": [
            {
                "plugin_id": domain["pluginId"],
                "plugin_version": domain["pluginVersion"],
                "instance_id": domain["instanceId"],
                "tools": [
                    {
                        "capability_id": tool["capabilityId"],
                        "shadow_name": tool["shadowName"],
                        "runtime_name": tool["name"],
                        "risk_level": tool["riskLevel"],
                        "result_mode": tool["resultMode"],
                        "max_result_bytes": tool["maxResultBytes"],
                        "max_model_chars": tool["maxModelChars"],
                    }
                    for tool in domain["tools"]
                ],
                "skills": [skill["name"] for skill in domain["skills"]],
            }
            for domain in compiled_domains
        ],
    }
    (target / "shadow-runtime-manifest.json").write_text(
        json.dumps(runtime_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "agent-bundle.lock").write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic Shadow DSH bundle")
    parser.add_argument("--platform-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        target = build_bundle(
            platform_root=args.platform_root.resolve(),
            profile_path=args.profile.resolve(),
            instances_path=args.instances.resolve(),
            plugin_roots=[path.resolve() for path in args.plugin_root],
            output_dir=args.output_dir.resolve(),
        )
    except PluginContractError as exc:
        parser.error(str(exc))
    print(target)


if __name__ == "__main__":
    main()
