from __future__ import annotations

import enum

class PermissionMode(enum.Enum):
    DEFAULT = "default"
    PLAN = "plan"
    EDIT = "edit"
    BYPASS = "bypass"

class ToolKind(enum.Enum):
    READ_ONLY = "read_only"
    FILE_EDIT = "file_edit"
    OTHER = "other"

    