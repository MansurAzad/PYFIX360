"""REST clients for the AI app-builder platforms PYFIX can drive.

Only the stdlib is used (``urllib``), so the application keeps its
"offline-first, no heavy dependencies" character: the offline workflow still
needs nothing installed.

Two operating modes exist across the platforms we know:

* **API** — a documented REST surface. This is the stable path: it is
  versioned, it does not break when the web UI is redesigned, and it is the
  only one that can be scripted.
* **browser** — driving a signed-in web session. It works for the platforms
  with no public API, but every selector is a guess about the current markup
  and it breaks the day the UI changes. See
  :mod:`pyfix360.integrations.browser`.

Honesty rules:

* Every endpoint here was read from the vendor's own documentation. None of
  it has been executed against a live account from inside this build, so the
  catalogue entry for each platform keeps ``verified_live = False``.
* The clients raise :class:`UnsupportedOperation` rather than pretending. If
  the API cannot do something — Lovable's public API cannot build an app from
  a prompt, for instance — the error names the browser path instead of
  silently doing nothing.
* Nothing here is called implicitly. A client only runs when the user asks
  for the operation, through the service layer that applies the privacy and
  access gates.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class PlatformError(Exception):
    """Base for every failure raised by a platform client."""


class MissingCredential(PlatformError):
    """No API key is stored for this platform."""


class UnsupportedOperation(PlatformError):
    """The platform's API cannot do this. Use the browser path instead."""


class RemoteCallFailed(PlatformError):
    """The remote service refused the request or answered with an error."""


@dataclass
class CallResult:
    """The outcome of one platform operation."""

    handle: str = ""      # id / URL that identifies the ongoing work
    status: str = ""      # vendor-reported status, when there is one
    output: str = ""      # the text answer, when there is one
    raw: dict = field(default_factory=dict)


class PlatformClient:
    """Base class: one HTTP surface for one platform."""

    key = ""
    label = ""
    api_base = ""
    key_header = "Authorization: Bearer"
    key_query_param = ""

    #: does the API accept a natural-language prompt and start work?
    supports_prompt = False
    #: can a follow-up message be sent to work that is already running?
    supports_followup = False
    #: can work be polled for status / output?
    supports_status = False

    def __init__(self, api_key: str = "", timeout_s: int = 60) -> None:
        self.api_key = api_key or ""
        self.timeout_s = timeout_s

    # -- plumbing ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return self.api_base.rstrip("/") + path

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            if self.key_query_param:
                pass  # the key travels in the query string, not in a header
            else:
                headers[self.key_header.split(":")[0].strip()] = (
                    f"{self.key_header.split(':', 1)[1].strip()} {self.api_key}"
                    if ":" in self.key_header else self.api_key
                )
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 query: dict | None = None, headers: dict[str, str] | None = None,
                 raw_body: str | None = None) -> dict:
        url = self._url(path)
        params = {k: v for k, v in (query or {}).items() if v not in (None, "")}
        if self.api_key and self.key_query_param:
            params[self.key_query_param] = self.api_key
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = None
        req_headers = self._headers(headers)
        if raw_body is not None:
            data = raw_body.encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(url, data=data, headers=req_headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise RemoteCallFailed(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RemoteCallFailed(f"network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RemoteCallFailed("the request timed out") from exc

        if not payload.strip():
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {"_text": payload}
        return parsed if isinstance(parsed, dict) else {"_data": parsed}

    # -- operations -------------------------------------------------------
    def submit_prompt(self, prompt: str, *, system: str = "") -> CallResult:
        raise UnsupportedOperation(
            f"{self.label}'s API does not accept a build prompt. Use the "
            "browser path for this platform.")

    def send_followup(self, handle: str, message: str) -> CallResult:
        raise UnsupportedOperation(
            f"{self.label}'s API does not accept follow-up messages on an "
            "existing job.")

    def status(self, handle: str) -> CallResult:
        raise UnsupportedOperation(
            f"{self.label}'s API does not expose a status endpoint.")

    def describe(self) -> str:
        return (f"{self.label} — {self.api_base or 'no API'} "
                f"(prompt={self.supports_prompt}, "
                f"followup={self.supports_followup}, "
                f"status={self.supports_status})")


# ======================================================================================
# v0 (Vercel)
# ======================================================================================


class V0Client(PlatformClient):
    """v0 platform API — https://api.v0.dev/v1.

    The v1 surface creates a chat from a prompt and reads the messages back.
    A later v2 surface adds deployment creation; both hosts are accepted, so
    the version is pinned per client instance.
    """

    key = "v0"
    label = "v0"
    api_base = "https://api.v0.dev/v1"
    key_header = "Authorization: Bearer"
    supports_prompt = True
    supports_followup = True
    supports_status = True

    def __init__(self, api_key: str = "", timeout_s: int = 60,
                 api_version: str = "v1", model: str = "") -> None:
        super().__init__(api_key, timeout_s)
        self.api_version = api_version
        self.model = model

    def submit_prompt(self, prompt: str, *, system: str = "") -> CallResult:
        body: dict = {"message": f"{system}\n\n{prompt}" if system else prompt}
        if self.model:
            body["model"] = self.model
        data = self._request("POST", "/chats", body=body)
        chat_id = str(data.get("id") or data.get("chatId") or data.get("chat_id") or "")
        return CallResult(handle=chat_id, status=str(data.get("status") or ""),
                          raw=data)

    def send_followup(self, handle: str, message: str) -> CallResult:
        if not handle:
            raise UnsupportedOperation("a chat id is required to send a follow-up")
        data = self._request("POST", f"/chats/{handle}/messages",
                             body={"message": message})
        return CallResult(handle=handle, output=_text_of(data), raw=data)

    def status(self, handle: str) -> CallResult:
        data = self._request("GET", f"/chats/{handle}")
        return CallResult(handle=handle,
                          status=str(data.get("status") or ""),
                          output=_text_of(data), raw=data)

    def messages(self, handle: str) -> CallResult:
        data = self._request("GET", f"/chats/{handle}/messages")
        return CallResult(handle=handle, output=_text_of(data), raw=data)

    def preview_url(self, handle: str) -> CallResult:
        data = self._request("GET", f"/chats/{handle}/preview")
        url = str(data.get("url") or data.get("previewUrl") or "")
        return CallResult(handle=handle, output=url, raw=data)


# ======================================================================================
# Lovable
# ======================================================================================


class LovableClient(PlatformClient):
    """Lovable public REST API — https://api.lovable.dev.

    The public API manages and deploys *existing* projects. It does not build
    an app from a prompt, and the calls it does make do not consume AI build
    credits. Prompting therefore belongs to the browser path.
    """

    key = "lovable"
    label = "Lovable"
    api_base = "https://api.lovable.dev/v1"
    key_header = "Lovable-API-Key"
    supports_prompt = False
    supports_followup = False
    supports_status = True

    def list_projects(self) -> CallResult:
        data = self._request("GET", "/projects")
        return CallResult(output=_text_of(data), raw=data)

    def get_project(self, project_id: str) -> CallResult:
        data = self._request("GET", f"/projects/{project_id}")
        return CallResult(handle=project_id, output=_text_of(data), raw=data)

    def deploy(self, project_id: str) -> CallResult:
        data = self._request("POST", f"/projects/{project_id}/deployments")
        return CallResult(handle=project_id,
                          status=str(data.get("status") or ""), raw=data)

    def status(self, handle: str) -> CallResult:
        return self.get_project(handle)


# ======================================================================================
# Replit
# ======================================================================================


class ReplitClient(PlatformClient):
    """Replit GraphQL API — https://api.replit.com/graphql.

    The operation names below are the community-documented ones for creating a
    workspace and talking to the Agent. The official Agent documentation does
    not publish them, so treat this client as community-documented and verify
    it against a live account before relying on it.
    """

    key = "replit"
    label = "Replit"
    api_base = "https://api.replit.com/graphql"
    key_header = "Authorization: Bearer"
    supports_prompt = True
    supports_followup = True
    supports_status = True

    CREATE_WORKSPACE = """
    mutation CreateWorkspace($title: String!, $agentId: String, $template: String) {
      createWithHost(title: $title, agentId: $agentId, template: $template) {
        id
        title
        url
      }
    }
    """

    POST_AGENT_MESSAGE = """
    mutation PostAgentMessage($replId: String!, $message: String!) {
      postAgentMessage(replId: $replId, message: $message) {
        id
        role
        text
      }
    }
    """

    GET_CONVERSATION = """
    query GetConversation($replId: String!) {
      conversationForRepl(replId: $replId) {
        id
        title
        messages { id role text }
      }
    }
    """

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        data = self._request("POST", "", body={"query": query,
                                               "variables": variables or {}})
        if data.get("errors"):
            raise RemoteCallFailed(f"GraphQL error: {json.dumps(data['errors'])[:600]}")
        return data.get("data") or {}

    def create_workspace(self, title: str, *, agent_id: str = "",
                         template: str = "") -> CallResult:
        data = self.graphql(self.CREATE_WORKSPACE, {
            "title": title, "agentId": agent_id or None, "template": template or None,
        })
        node = (data.get("createWithHost") or {})
        return CallResult(handle=str(node.get("id") or ""),
                          output=str(node.get("url") or ""), raw=data)

    def submit_prompt(self, prompt: str) -> CallResult:
        """Send the prompt to the Agent of an existing workspace."""
        raise UnsupportedOperation(
            "Replit's Agent needs a workspace before it accepts a message. "
            "Call create_workspace() first, then post_agent_message().")

    def post_agent_message(self, repl_id: str, message: str) -> CallResult:
        data = self.graphql(self.POST_AGENT_MESSAGE,
                             {"replId": repl_id, "message": message})
        node = (data.get("postAgentMessage") or {})
        return CallResult(handle=repl_id, output=str(node.get("text") or ""),
                          raw=data)

    def conversation(self, repl_id: str) -> CallResult:
        data = self.graphql(self.GET_CONVERSATION, {"replId": repl_id})
        conv = (data.get("conversationForRepl") or {})
        return CallResult(handle=repl_id, output=_text_of(conv), raw=data)

    def status(self, handle: str) -> CallResult:
        return self.conversation(handle)


# ======================================================================================
# Builder.io
# ======================================================================================


class BuilderClient(PlatformClient):
    """Builder.io Content API — https://cdn.builder.io/api/v3.

    The Content API serves and publishes *visual content*. It is not the
    code-generation path: Figma-to-code runs through the Figma plugin and the
    ``builder`` CLI, or through Builder's MCP server.
    """

    key = "builder"
    label = "Builder.io"
    api_base = "https://cdn.builder.io/api/v3"
    key_header = ""
    key_query_param = "apiKey"
    supports_prompt = False
    supports_followup = False
    supports_status = True

    def get_content(self, model: str, url: str = "/") -> CallResult:
        data = self._request("GET", f"/content/{model}", query={"url": url})
        return CallResult(output=_text_of(data), raw=data)

    def publish_content(self, model: str, payload: dict,
                        url: str = "/") -> CallResult:
        data = self._request("POST", f"/content/{model}",
                             body={"value": payload, "url": url})
        return CallResult(output=_text_of(data), raw=data)

    def status(self, handle: str) -> CallResult:
        return self.get_content(handle)


# ======================================================================================
# Google AI Studio
# ======================================================================================


class AIStudioClient(PlatformClient):
    """Gemini through the OpenAI-compatible surface of AI Studio.

    This is the one platform whose API genuinely takes a prompt and returns
    an answer, so it is also the one where the API path can replace the web
    workbench for most tasks.
    """

    key = "ai-studio"
    label = "Google AI Studio"
    api_base = "https://generativelanguage.googleapis.com/v1beta/openai"
    key_header = "x-goog-api-key"
    supports_prompt = True
    supports_followup = True
    supports_status = True

    def __init__(self, api_key: str = "", timeout_s: int = 60,
                 model: str = "gemini-2.5-flash") -> None:
        super().__init__(api_key, timeout_s)
        self.model = model

    def list_models(self) -> CallResult:
        data = self._request("GET", "/models")
        return CallResult(output=_text_of(data), raw=data)

    def submit_prompt(self, prompt: str, *, system: str = "",
                      model: str = "") -> CallResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": model or self.model, "messages": messages,
                "temperature": 0.2}
        data = self._request("POST", "/chat/completions", body=body)
        answer = ""
        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            answer = _text_of(data)
        return CallResult(handle=model or self.model, status="ok",
                          output=answer, raw=data)

    def send_followup(self, handle: str, message: str) -> CallResult:
        return self.submit_prompt(message, model=handle)

    def status(self, handle: str) -> CallResult:
        return self.list_models()


# ======================================================================================
# registry
# ======================================================================================

CLIENTS: dict[str, type[PlatformClient]] = {
    V0Client.key: V0Client,
    LovableClient.key: LovableClient,
    ReplitClient.key: ReplitClient,
    BuilderClient.key: BuilderClient,
    AIStudioClient.key: AIStudioClient,
}

#: Platforms whose only operating path is a signed-in web session.
BROWSER_ONLY: tuple[str, ...] = ("trickle", "emergent")


def credential_name(platform_key: str) -> str:
    """The keyring entry that holds this platform's API key."""
    return f"platform-{platform_key}"


def client_for(platform_key: str, api_key: str = "", timeout_s: int = 60) -> PlatformClient:
    """Build the client for a platform.

    Raises :class:`UnsupportedOperation` for a platform with no API and
    :class:`MissingCredential` when a key is required but absent.
    """
    if platform_key in BROWSER_ONLY:
        raise UnsupportedOperation(
            f"'{platform_key}' publishes no API. It has to be driven through a "
            "signed-in web session (browser automation)."
        )
    cls = CLIENTS.get(platform_key)
    if cls is None:
        raise UnsupportedOperation(
            f"no API client is implemented for '{platform_key}'."
        )
    if not api_key:
        raise MissingCredential(
            f"no API key is stored for '{platform_key}'. Add it on the "
            "Integrations screen first."
        )
    return cls(api_key, timeout_s)


def _text_of(data: object) -> str:
    """Best-effort extraction of a human-readable body from a JSON payload."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "output", "content", "message", "answer"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(data, ensure_ascii=False)[:4000]
    return json.dumps(data, ensure_ascii=False)[:4000]
