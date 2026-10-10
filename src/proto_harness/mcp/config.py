from __future__ import annotations
import json
import logging
import os
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$(?:\{(\w+)\}|(\w+))")

def _get_env_val(var_name: str) -> str:
    """Retrieve an environment variable from os.environ or candidate .env files."""
    if var_name in os.environ and os.environ[var_name]:
        return os.environ[var_name]
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / "src" / "proto_harness" / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for p in candidates:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == var_name:
                        return v.strip().strip('"').strip("'")
            except Exception:
                pass
    return ""

def _expand_env_vars(value: str) -> str:
    """Expand environment variable references like ${API_KEY} or $PORT."""
    def _repl(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        return _get_env_val(var_name)
    return _ENV_VAR_PATTERN.sub(_repl, value)

class MCPServerConfig(BaseModel):
    command: str|None=None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    transport: Literal["stdio", "sse", "streamable_http"] | None = None
    enabled: bool=True

    read_only_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_transport_and_expand(self) ->MCPServerConfig:
        if not self.command and not self.url:
            raise ValueError("Either command or url must be provided for an MCP server")
        if self.transport is None:
            self.transport = "sse" if self.url else "stdio"
        if self.command:
            self.command = _expand_env_vars(self.command)
        self.args = [_expand_env_vars(arg) for arg in self.args]
        self.env = {k: _expand_env_vars(v) for k, v in self.env.items()}
        if self.url:
            self.url = _expand_env_vars(self.url)
        self.headers = {k: _expand_env_vars(v) for k, v in self.headers.items()}
        return self



class MCPConfig(BaseModel):
    """Top-level MCP configuration mapping server names to server configurations."""
    # Accepts both "mcpServers" (industry standard) and "mcp_servers"
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict, alias="mcpServers")
    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }


def find_mcp_config_file(cwd: Path | None = None) -> Path | None:
    """Discover the highest-priority MCP configuration file."""
    target_cwd = (cwd or Path.cwd()).resolve()
    candidates = [
        target_cwd / ".proto_harness" / "mcp.json",
        target_cwd / "mcp_config.json",
        target_cwd / ".mcp.json",
        target_cwd / "src" / "proto_harness" / ".mcp.json",
        target_cwd / "src" / ".mcp.json",
        target_cwd.parent / ".mcp.json",
        Path.home() / ".proto_harness" / "mcp.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None

    
def load_mcp_config(cwd: Path | None = None) -> MCPConfig:
    """Load and parse the active MCP configuration, or return empty config if none exists."""
    config_path = find_mcp_config_file(cwd)
    if not config_path:
        return MCPConfig()
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        return MCPConfig.model_validate(data)
    except Exception as exc:
        logger.warning("Failed parsing MCP configuration from %s: %s", config_path, exc)
        return MCPConfig()
