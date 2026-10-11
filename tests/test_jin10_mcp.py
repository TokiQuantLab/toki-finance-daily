"""Offline MCP transport, schema, credential and business-result contracts."""
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
import json
import os
import traceback

from toki_finance_daily.jin10_mcp import ENDPOINT, Jin10MCPClient, Jin10MCPError, fetch_jin10_mcp, load_jin10_token


TOKEN = "offline-fixture-secret"
TOOLS = [{"name": "list_flash", "inputSchema": {"type": "object", "properties": {"cursor": {"type": "string"}}, "additionalProperties": False}, "annotations": {"readOnlyHint": True}},
         {"name": "list_calendar", "inputSchema": {"type": "object", "additionalProperties": False}},
         {"name": "get_quote", "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False}},
         {"name": "get_kline", "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}, "count": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["code"], "additionalProperties": False}}]


class Response:
    def __init__(self, body=b"", *, content_type="application/json", headers=None, status=200):
        self.parts = list(body) if isinstance(body, list) else [body]
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read1(self, size):
        if not self.parts:
            return b""
        value = self.parts.pop(0)
        if len(value) > size:
            self.parts.insert(0, value[size:])
        return value[:size]


class Endpoint:
    """Request-aware fake server; its requests never leave the test process."""
    def __init__(self, tool_result=None, *, sse=False, protocol="2025-11-25", callback=None):
        self.requests = []
        self.tool_result = tool_result if tool_result is not None else {"structuredContent": {"status": 200, "message": "OK", "data": {"items": [{"id": "free"}], "next_cursor": "page-two", "has_more": True}}, "content": []}
        self.sse, self.protocol, self.callback = sse, protocol, callback

    def open(self, request, timeout):
        message = json.loads(request.data)
        self.requests.append((request, message, timeout))
        if self.callback:
            override = self.callback(message)
            if override is not None:
                return override
        method, identifier = message["method"], message.get("id")
        headers = {}
        if method == "initialize":
            result = {"protocolVersion": self.protocol, "capabilities": {"tools": {}, "resources": {}}}
            headers["Mcp-Session-Id"] = "fixture-session"
        elif method == "notifications/initialized":
            return Response(status=202)
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "resources/list":
            result = {"resources": [{"uri": "quote://codes"}]}
        elif method == "tools/call":
            result = self.tool_result
        elif method == "resources/read":
            result = {"contents": [{"uri": "quote://codes", "text": "公开品种表"}]}
        else:
            raise AssertionError("Unexpected method")
        response = {"jsonrpc": "2.0", "id": identifier, "result": result}
        if self.sse and method == "tools/call":
            # Other IDs and progress notifications cannot become this call's result.
            prelude = b': keepalive\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\r\n\r\ndata: {"jsonrpc":"2.0","id":999,"result":{"wrong":true}}\r\n\r\n'
            text = json.dumps(response, ensure_ascii=False, indent=2)
            stream = prelude + ("event: message\r\n" + "\r\n".join("data: " + line for line in text.splitlines()) + "\r\n\r\n").encode()
            return Response([stream[index:index + 7] for index in range(0, len(stream), 7)], content_type="text/event-stream")
        return Response(json.dumps(response, ensure_ascii=False).encode(), headers=headers)


class Jin10MCPTests(TestCase):
    def test_handshake_auth_session_negotiation_and_single_page_safe_envelope(self):
        endpoint = Endpoint(protocol="2025-06-18")
        source = {"url": ENDPOINT, "mcp_tool": "list_flash", "mcp_arguments": {"cursor": "previous-page"}}
        with patch.dict(os.environ, {"JIN10_MCP_TOKEN": TOKEN}), patch("toki_finance_daily.jin10_mcp.build_opener", return_value=endpoint):
            raw, metadata = fetch_jin10_mcp(source, {"timeout_seconds": 8, "retries": 3})
        methods = [message["method"] for _, message, _ in endpoint.requests]
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list", "resources/list", "tools/call"])
        self.assertNotIn("id", endpoint.requests[1][1])
        self.assertEqual(endpoint.requests[0][1]["params"]["protocolVersion"], "2025-11-25")
        for index, (request, message, timeout) in enumerate(endpoint.requests):
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
            self.assertEqual(headers["content-type"], "application/json")
            self.assertIn("text/event-stream", headers["accept"])
            self.assertEqual(timeout, 8)
            if index:
                self.assertEqual(headers["mcp-session-id"], "fixture-session")
                self.assertEqual(headers["mcp-protocol-version"], "2025-06-18")
        self.assertEqual(endpoint.requests[-1][1]["params"], {"name": "list_flash", "arguments": {"cursor": "previous-page"}})
        envelope = json.loads(raw)
        self.assertTrue(envelope["result"]["data"]["has_more"])
        self.assertEqual(envelope["protocolVersion"], "2025-06-18")
        self.assertEqual(envelope["tool_schema"], TOOLS[0]["inputSchema"])
        self.assertEqual(metadata["fetched_at"], envelope["fetched_at"])
        self.assertTrue(metadata["fetched_at"].endswith("+00:00"))
        self.assertNotIn(TOKEN, raw.decode() + json.dumps(metadata))
        self.assertNotIn("fixture-session", raw.decode() + json.dumps(metadata))

    def test_sse_matches_rpc_id_and_structured_result_wins_over_text(self):
        structured = {"status": 200, "message": "正常", "data": [{"title": "中国数据"}]}
        response = {"structuredContent": structured, "content": [{"type": "text", "text": '{"status":401,"data":"wrong"}'}, {"type": "text", "text": TOKEN}]}
        endpoint = Endpoint(response, sse=True)
        result = Jin10MCPClient(TOKEN, opener=endpoint).call_tool("list_calendar")
        self.assertEqual(result["result"], structured)
        self.assertEqual(result["content"][0]["text"], response["content"][0]["text"])
        self.assertEqual(result["content"][1]["text"], "[redacted]")
        self.assertEqual(len(endpoint.requests), 5)

    def test_text_json_is_supplemental_and_cannot_replace_missing_structured_content(self):
        for response in ({"content": [{"type": "text", "text": '{"status":200,"data":[]}'}]}, {"structuredContent": None, "content": []}):
            with self.subTest(response=response):
                endpoint = Endpoint(response)
                with self.assertRaisesRegex(Jin10MCPError, "structuredContent"):
                    Jin10MCPClient(TOKEN, opener=endpoint).call_tool("list_calendar")
                self.assertEqual(len(endpoint.requests), 5)
        # An explicitly empty object is still structured and is never replaced by text JSON.
        result = Jin10MCPClient(TOKEN, opener=Endpoint({"structuredContent": {}, "content": [{"type": "text", "text": '{"data":"replacement"}'}]})).call_tool("list_calendar")
        self.assertEqual(result["result"], {})

    def test_discovered_schema_rejects_offset_wrong_types_required_and_bounds_before_call(self):
        for name, arguments in [("list_flash", {"offset": 20}), ("list_flash", {"cursor": 20}), ("list_calendar", {"cursor": "x"}),
                                ("get_quote", {}), ("get_kline", {"code": "XAUUSD", "count": True}), ("get_kline", {"code": "XAUUSD", "count": 101})]:
            with self.subTest(name=name, arguments=arguments):
                endpoint = Endpoint()
                with self.assertRaises(Jin10MCPError):
                    Jin10MCPClient(TOKEN, opener=endpoint).call_tool(name, arguments)
                self.assertEqual(len(endpoint.requests), 4)
                self.assertNotIn("tools/call", [message["method"] for _, message, _ in endpoint.requests])
        endpoint = Endpoint()
        client = Jin10MCPClient(TOKEN, opener=endpoint)
        with self.assertRaises(Jin10MCPError):
            client.call_tool("place_order", {})
        with self.assertRaises(Jin10MCPError):
            client.read_resource("file:///private")
        self.assertEqual(endpoint.requests, [])
        self.assertEqual(client.read_resource()["contents"][0]["uri"], "quote://codes")

    def test_rpc_http_iserror_and_business_status_stop_without_retry_or_secret_errors(self):
        def rpc_error(message):
            if message["method"] == "initialize":
                return Response(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32600, "message": TOKEN}}).encode())
        def http_error(message):
            raise HTTPError(ENDPOINT, 401, TOKEN, {}, None)
        cases = [(Endpoint(callback=rpc_error), 1), (Endpoint(callback=http_error), 1),
                 (Endpoint({"isError": True, "structuredContent": {"status": 200}, "content": [{"type": "text", "text": TOKEN}]}), 5),
                 (Endpoint({"structuredContent": {"status": 401, "message": TOKEN}, "content": []}), 5)]
        for endpoint, expected_requests in cases:
            with self.subTest(requests=expected_requests):
                try:
                    Jin10MCPClient(TOKEN, opener=endpoint).call_tool("list_calendar")
                except Jin10MCPError as error:
                    self.assertNotIn(TOKEN, str(error))
                    self.assertNotIn(TOKEN, "".join(traceback.format_exception(type(error), error, error.__traceback__)))
                else:
                    self.fail("Expected immediate MCP failure")
                self.assertEqual(len(endpoint.requests), expected_requests)

    def test_credentials_environment_priority_empty_fallback_and_private_file(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.json"
            path.write_text(json.dumps({"JIN10_MCP_TOKEN": "file-fixture-secret"}))
            path.chmod(0o600)
            with patch.dict(os.environ, {"JIN10_MCP_TOKEN": TOKEN, "TOKI_DAILY_CREDENTIALS_FILE": str(path)}):
                self.assertEqual(load_jin10_token(), TOKEN)
            with patch.dict(os.environ, {"JIN10_MCP_TOKEN": " ", "TOKI_DAILY_CREDENTIALS_FILE": str(path)}):
                self.assertEqual(load_jin10_token(), "file-fixture-secret")
                if os.name == "posix":
                    path.chmod(0o644)
                    with self.assertRaisesRegex(Jin10MCPError, "private"):
                        load_jin10_token()
            with patch.dict(os.environ, {"JIN10_MCP_TOKEN": "invalid\nheader"}):
                with self.assertRaises(Jin10MCPError):
                    load_jin10_token()

    def test_endpoint_redirect_policy_response_budgets_and_wrong_rpc_ids(self):
        endpoint = Endpoint()
        with self.assertRaisesRegex(Jin10MCPError, "official HTTPS"):
            Jin10MCPClient(TOKEN, endpoint="https://example.org/mcp", opener=endpoint)
        self.assertEqual(endpoint.requests, [])
        client = Jin10MCPClient(TOKEN)
        redirect = next(handler for handler in client._opener.handlers if handler.__class__.__name__ == "_NoRedirect")
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://example.org/"))
        def oversized(message):
            return Response(b"x" * 1025)
        with self.assertRaisesRegex(Jin10MCPError, "byte budget"):
            Jin10MCPClient(TOKEN, opener=Endpoint(callback=oversized), max_bytes=1024).connect()
        def wrong_id(message):
            return Response(json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode())
        with self.assertRaisesRegex(Jin10MCPError, "JSON-RPC ID"):
            Jin10MCPClient(TOKEN, opener=Endpoint(callback=wrong_id)).connect()
