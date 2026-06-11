from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ValidationError(ValueError):
    pass


def require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"字段 {key} 必须是非空字符串")
    return value.strip()


def require_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"字段 {key} 必须是整数")
    return value


def optional_options(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("options", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("字段 options 必须是对象")
    allowed = {"repeat_count", "max_workers", "retry_count"}
    return {key: value[key] for key in allowed if key in value}


@dataclass(frozen=True)
class CreateBatchInput:
    project_id: int
    model_ids: list[int]
    csv_text: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CreateBatchInput":
        project_id = require_int(data, "project_id")
        model_ids = data.get("model_ids")
        if not isinstance(model_ids, list) or not model_ids:
            raise ValidationError("字段 model_ids 必须是非空整数数组")
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in model_ids):
            raise ValidationError("字段 model_ids 必须是非空整数数组")
        csv_text = data.get("csv_text", "")
        if csv_text is None:
            csv_text = ""
        if not isinstance(csv_text, str):
            raise ValidationError("字段 csv_text 必须是字符串")
        return cls(project_id=project_id, model_ids=model_ids, csv_text=csv_text, options=optional_options(data))

    def to_agent_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "model_ids": self.model_ids,
            "csv_text": self.csv_text,
            "options": self.options,
        }


@dataclass(frozen=True)
class BatchIdInput:
    batch_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchIdInput":
        return cls(batch_id=require_string(data, "batch_id"))


@dataclass(frozen=True)
class ExportBatchInput:
    batch_id: str
    format: str = "xls"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExportBatchInput":
        batch_id = require_string(data, "batch_id")
        export_format = data.get("format", "xls")
        if export_format != "xls":
            raise ValidationError("当前只支持 xls 导出")
        return cls(batch_id=batch_id, format=export_format)


@dataclass(frozen=True)
class RerunFailedInput:
    batch_id: str
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RerunFailedInput":
        return cls(batch_id=require_string(data, "batch_id"), options=optional_options(data))


CREATE_BATCH_SCHEMA = {
    "type": "object",
    "required": ["project_id", "model_ids"],
    "properties": {
        "project_id": {"type": "integer", "description": "目标项目 ID"},
        "model_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 1,
            "description": "模型配置 ID 列表",
        },
        "csv_text": {"type": "string", "description": "可选 CSV 问题文本"},
        "options": {
            "type": "object",
            "properties": {
                "repeat_count": {"type": "integer", "minimum": 1},
                "max_workers": {"type": "integer", "minimum": 1},
                "retry_count": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

BATCH_ID_SCHEMA = {
    "type": "object",
    "required": ["batch_id"],
    "properties": {"batch_id": {"type": "string"}},
    "additionalProperties": False,
}

EXPORT_BATCH_SCHEMA = {
    "type": "object",
    "required": ["batch_id"],
    "properties": {
        "batch_id": {"type": "string"},
        "format": {"type": "string", "enum": ["xls"], "default": "xls"},
    },
    "additionalProperties": False,
}

RERUN_FAILED_SCHEMA = {
    "type": "object",
    "required": ["batch_id"],
    "properties": {
        "batch_id": {"type": "string"},
        "options": {
            "type": "object",
            "properties": {
                "max_workers": {"type": "integer", "minimum": 1},
                "retry_count": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

