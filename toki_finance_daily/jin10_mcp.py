"""Bounded, read-only Jin10 Streamable HTTP MCP client (no automatic retries)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import codecs
import json
import math
import os
import re
import stat
import time

ENDPOINT = "https://mcp.jin10.com/mcp"
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOLS = {"2025-03-26", "2025-06-18", PROTOCOL_VERSION}
READ_ONLY_TOOLS = frozenset({"get_quote", "get_kline", "list_flash", "search_flash", "list_news", "search_news", "get_news", "list_calendar"})


class Jin10MCPError(RuntimeError):
    """Credential, transport, protocol, schema or business failure; do not retry."""


def load_jin10_token():
    """Environment first; otherwise a private local credentials JSON file."""
    token = os.environ.get("JIN10_MCP_TOKEN", "").strip()
    if not token:
        location = os.environ.get("TOKI_DAILY_CREDENTIALS_FILE") or "~/.config/toki-finance-daily/credentials.json"
        path = Path(location).expanduser()
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise Jin10MCPError("Jin10 credentials must be a small regular JSON file")
            if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                raise Jin10MCPError("Jin10 credentials file must be private (chmod 600)")
            credentials = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Jin10MCPError("Cannot read Jin10 credentials; set JIN10_MCP_TOKEN or a private credentials JSON") from None
        token = credentials.get("JIN10_MCP_TOKEN") if isinstance(credentials, dict) else None
        token = token.strip() if isinstance(token, str) else ""
    if not token or any(character in token for character in "\r\n"):
        raise Jin10MCPError("JIN10_MCP_TOKEN is missing or invalid")
    return token


def _validate(value, schema, root, path="arguments", depth=0):
    """Validate the JSON Schema constraints used by discovered input schemas."""
    if depth > 16 or schema is False:
        raise Jin10MCPError(f"{path}: schema validation failed")
    if schema is True:
        return
    if not isinstance(schema, dict):
        raise Jin10MCPError("Tool inputSchema is invalid")
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise Jin10MCPError("Only local tool schema references are supported")
        target = root
        try:
            for key in reference[2:].split("/"):
                target = target[key.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError) as error:
            raise Jin10MCPError("Tool schema reference is missing") from error
        _validate(value, target, root, path, depth + 1)
    for branch in schema.get("allOf", []):
        _validate(value, branch, root, path, depth + 1)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for branch in schema[keyword]:
                try:
                    _validate(value, branch, root, path, depth + 1)
                    matches += 1
                except Jin10MCPError:
                    pass
            if not matches or keyword == "oneOf" and matches != 1:
                raise Jin10MCPError(f"{path}: does not match {keyword}")
    finite_number = (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and math.isfinite(value))
    checks = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
              "integer": isinstance(value, int) and not isinstance(value, bool),
              "number": finite_number,
              "boolean": isinstance(value, bool), "null": value is None}
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not isinstance(types, list) or not types or any(kind not in checks for kind in types):
            raise Jin10MCPError("Tool inputSchema has an unsupported type")
        if not any(checks[kind] for kind in types):
            raise Jin10MCPError(f"{path}: incorrect value type")
    if "enum" in schema and value not in schema["enum"] or "const" in schema and value != schema["const"]:
        raise Jin10MCPError(f"{path}: value is not allowed by the discovered schema")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise Jin10MCPError("Tool object schema is invalid")
        if any(key not in value for key in required):
            raise Jin10MCPError(f"{path}: required argument is missing")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], root, f"{path}.{key}", depth + 1)
            elif additional is False:
                raise Jin10MCPError(f"{path}: undeclared argument")
            elif isinstance(additional, dict):
                _validate(item, additional, root, f"{path}.{key}", depth + 1)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")):
            raise Jin10MCPError(f"{path}: string length is outside the discovered limits")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise Jin10MCPError(f"{path}: string does not match the discovered pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not finite_number or value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf")):
            raise Jin10MCPError(f"{path}: number is outside the discovered limits")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"] or "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise Jin10MCPError(f"{path}: number is outside the discovered limits")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise Jin10MCPError(f"{path}: array size is outside the discovered limits")
        if "items" in schema:
            for item in value:
                _validate(item, schema["items"], root, path + "[]", depth + 1)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward an Authorization header through an unexpected redirect.
        return None


class Jin10MCPClient:
    def __init__(self, token=None, *, endpoint=ENDPOINT, timeout=12, max_bytes=3000000, opener=None):
        address = urlsplit(endpoint)
        if (address.scheme != "https" or address.netloc != "mcp.jin10.com" or address.path != "/mcp"
                or address.query or address.fragment):
            raise Jin10MCPError("Jin10 credentials may only be sent to the official HTTPS MCP endpoint")
        if not 1 <= float(timeout) <= 60 or not 1024 <= int(max_bytes) <= 20000000:
            raise Jin10MCPError("MCP timeout/response byte limits are invalid")
        self._token = token if token is not None else load_jin10_token()
        if not isinstance(self._token, str) or not self._token or any(x in self._token for x in "\r\n"):
            raise Jin10MCPError("JIN10_MCP_TOKEN is missing or invalid")
        self.endpoint, self.timeout, self.max_bytes = endpoint, float(timeout), int(max_bytes)
        self._opener = opener or build_opener(_NoRedirect())
        self.protocol_version, self._session, self._next_id = PROTOCOL_VERSION, None, 1
        self.tools, self.resources, self._ready = {}, set(), False

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self._token, "[redacted]")
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        return value

    def _select(self, message, expected):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise Jin10MCPError("Invalid MCP JSON-RPC response")
        matching = type(message.get("id")) is int and message.get("id") == expected
        if "error" in message and (matching or expected is None or message.get("id") is None):
            error = message["error"]
            detail = error.get("message", "JSON-RPC error") if isinstance(error, dict) else "JSON-RPC error"
            raise Jin10MCPError("Jin10 JSON-RPC error: " + str(self._redact(detail))[:300])
        if not matching:
            return None
        if "result" not in message or not isinstance(message["result"], dict):
            raise Jin10MCPError("MCP response is missing its result object")
        return message["result"]

    def _read(self, response, expected):
        is_sse = "text/event-stream" in response.headers.get("Content-Type", "").lower()
        deadline, total, chunks = time.monotonic() + self.timeout, 0, []
        decoder, pending, event_data = codecs.getincrementaldecoder("utf-8")(), "", []
        while True:
            if time.monotonic() > deadline:
                raise Jin10MCPError("MCP response exceeded its time budget")
            chunk = response.read1(min(65536, self.max_bytes + 1 - total))
            if chunk:
                total += len(chunk)
                if total > self.max_bytes:
                    raise Jin10MCPError("MCP response exceeded its byte budget")
                if not is_sse:
                    chunks.append(chunk)
                    continue
                pending += decoder.decode(chunk)
            else:
                if not is_sse:
                    break
                pending += decoder.decode(b"", final=True) + "\n\n"
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                line = line.rstrip("\r")
                if line.startswith("data:"):
                    event_data.append(line[5:].lstrip(" "))
                elif not line and event_data:
                    message = json.loads("\n".join(event_data))
                    event_data = []
                    result = self._select(message, expected)
                    if result is not None:
                        return result
            if not chunk:
                break
        if is_sse:
            raise Jin10MCPError("MCP SSE ended without the requested JSON-RPC ID")
        body = b"".join(chunks)
        if not body and expected is None:
            return None
        result = self._select(json.loads(body), expected)
        if result is None and expected is not None:
            raise Jin10MCPError("MCP response did not match the requested JSON-RPC ID")
        return result

    def _post(self, method, params=None, *, notification=False):
        message = {"jsonrpc": "2.0", "method": method}
        expected = None
        if not notification:
            expected = self._next_id
            self._next_id += 1
            message["id"] = expected
        if params is not None:
            message["params"] = params
        headers = {"Authorization": "Bearer " + self._token, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": self.protocol_version}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        request = Request(self.endpoint, data=json.dumps(message, allow_nan=False).encode(), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    if not re.fullmatch(r"[!-~]{1,512}", session):
                        raise Jin10MCPError("MCP returned an invalid session header")
                    self._session = session
                if notification and response.status in {202, 204}:
                    return None
                return self._read(response, expected)
        except Jin10MCPError:
            raise
        except HTTPError as error:
            raise Jin10MCPError(f"Jin10 MCP HTTP {error.code}; request was not retried") from None
        except (URLError, OSError):
            raise Jin10MCPError("Jin10 MCP transport failed; request was not retried") from None
        except (ValueError, UnicodeError):
            raise Jin10MCPError("Jin10 MCP response is not valid JSON/UTF-8") from None

    def connect(self):
        if self._ready:
            return self
        initialized = self._post("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                "clientInfo": {"name": "TokiFinanceDaily", "version": "0.1"}})
        negotiated = initialized.get("protocolVersion")
        if negotiated not in SUPPORTED_PROTOCOLS:
            raise Jin10MCPError("Jin10 MCP negotiated an unsupported protocol version")
        self.protocol_version = negotiated
        self._post("notifications/initialized", notification=True)
        definitions = self._post("tools/list", {})
        tools = definitions.get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise Jin10MCPError("MCP tools/list did not provide tool definitions")
        for tool in tools:
            name = tool.get("name")
            if name in READ_ONLY_TOOLS:
                if name in self.tools or not isinstance(tool.get("inputSchema"), dict):
                    raise Jin10MCPError("MCP tool schema is missing or duplicated")
                self.tools[name] = tool
        resource_list = self._post("resources/list", {}).get("resources")
        if not isinstance(resource_list, list) or any(not isinstance(resource, dict) for resource in resource_list):
            raise Jin10MCPError("MCP resources/list did not provide resource definitions")
        self.resources = {resource.get("uri") for resource in resource_list if resource.get("uri") == "quote://codes"}
        self._ready = True
        return self

    def call_tool(self, name, arguments=None):
        if name not in READ_ONLY_TOOLS:
            raise Jin10MCPError("Only the eight approved read-only Jin10 tools may be called")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise Jin10MCPError("MCP tool arguments must be an object")
        try:
            if len(json.dumps(arguments, allow_nan=False).encode()) > 16384:
                raise Jin10MCPError("MCP tool arguments are too large")
        except (ValueError, TypeError) as error:
            raise Jin10MCPError("MCP tool arguments must be finite JSON values") from error
        self.connect()
        tool = self.tools.get(name)
        if not tool or tool.get("annotations", {}).get("readOnlyHint") is False:
            raise Jin10MCPError("Requested read-only tool is unavailable in the discovered schema")
        schema = tool["inputSchema"]
        try:
            _validate(arguments, schema, schema)
        except Jin10MCPError as error:
            raise Jin10MCPError(str(self._redact(str(error)))) from None
        response = self._post("tools/call", {"name": name, "arguments": arguments})
        content = response.get("content", [])
        if not isinstance(content, list):
            raise Jin10MCPError("MCP tool content is not a list")
        if response.get("isError"):
            text = next((item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"), "Tool reported a business error")
            raise Jin10MCPError("Jin10 MCP tool error: " + str(self._redact(text))[:300])
        if "structuredContent" not in response:
            raise Jin10MCPError("MCP tool result requires structuredContent; text content is supplemental only")
        result = response["structuredContent"]
        if not isinstance(result, dict):
            raise Jin10MCPError("MCP structuredContent must be an object")
        if isinstance(result, dict) and "status" in result and str(result["status"]) != "200":
            raise Jin10MCPError("Jin10 business error: " + str(self._redact(result.get("message", "Non-success status")))[:300])
        return {"result": self._redact(result), "content": self._redact(content)}

    def read_resource(self, uri="quote://codes"):
        if uri != "quote://codes":
            raise Jin10MCPError("Only the approved quote://codes resource may be read")
        self.connect()
        if uri not in self.resources:
            raise Jin10MCPError("quote://codes is absent from discovered resources")
        return self._redact(self._post("resources/read", {"uri": uri}))


def fetch_jin10_mcp(source, network):
    """Collect exactly one schema-validated tool page; pagination stays explicit.

    Source: ``mcp_tool`` and ``mcp_arguments``. Output is a credential-free JSON
    envelope; structuredContent is authoritative and content is supplemental.
    Existing fetch_source owns caching, including failed attempts.
    """
    name = source.get("mcp_tool")
    if name not in READ_ONLY_TOOLS:
        raise Jin10MCPError("Source must select one approved read-only mcp_tool")
    client = Jin10MCPClient(endpoint=source.get("url", ENDPOINT), timeout=network.get("timeout_seconds", 12),
                            max_bytes=network.get("max_bytes", 3000000))
    response = client.call_tool(name, source.get("mcp_arguments", {}))
    fetched_at = datetime.now(timezone.utc).isoformat()
    envelope = {"tool": name, "result": response["result"], "protocolVersion": client.protocol_version,
                "fetched_at": fetched_at, "tool_schema": client._redact(client.tools[name]["inputSchema"])}
    if response["content"]:
        envelope["content"] = response["content"]
    metadata = {"transport": "jin10-mcp-streamable-http", "content_type": "application/json",
                "fetched_at": fetched_at, "mcp_protocol_version": client.protocol_version, "mcp_tool": name}
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode(), metadata
