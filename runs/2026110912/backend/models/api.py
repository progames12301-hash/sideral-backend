from __future__ import annotations

import json
from dataclasses import dataclass,field
from pathlib import Path
from typing import Any

from .service import BadRequest, ModelService, ServiceError


@dataclass(slots=True)
class ApiResponse:
    status: int
    body: bytes
    content_type: str="application/json; charset=utf-8"
    headers: dict[str,str]=field(default_factory=dict)

    @classmethod
    def json(cls,status: int,payload: Any,headers: dict[str,str] | None=None) -> "ApiResponse":
        return cls(status,json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8"),headers=headers or {})


class ModelApi:
    def __init__(self,data_dir: Path | None=None,cache_dir: Path | None=None,remote_enabled: bool | None=None) -> None:
        kwargs={}
        if data_dir is not None:kwargs["data_dir"]=data_dir
        if cache_dir is not None:kwargs["cache_dir"]=cache_dir
        if remote_enabled is not None:kwargs["remote_enabled"]=remote_enabled
        self.service=ModelService(**kwargs)

    @staticmethod
    def _one(query: dict[str,list[str]],name: str,default: str="") -> str:
        values=query.get(name)
        return str(values[0] if values else default).strip()

    def dispatch(self,path: str,query: dict[str,list[str]]) -> ApiResponse:
        try:
            if path == "/api/models":payload=self.service.catalog();return ApiResponse.json(200,payload)
            if path == "/api/models/status":return ApiResponse.json(200,self.service.statuses())
            if path == "/api/models/runs":return ApiResponse.json(200,self.service.runs(self._one(query,"model")))
            if path == "/api/models/products":return ApiResponse.json(200,self.service.products(self._one(query,"model"),self._one(query,"run") or None))
            if path == "/api/multimodel/runs":return ApiResponse.json(200,self.service.multimodel_runs(self._one(query,"product","qpf24")))
            if path == "/api/models/frame":
                body,content_type,headers=self.service.frame(self._one(query,"model"),self._one(query,"run"),self._one(query,"product"),self._one(query,"region","brazil"),self._one(query,"fh","0"),metadata_only=self._one(query,"format").lower()=="json")
                return ApiResponse.json(200,body,headers) if isinstance(body,dict) else ApiResponse(200,body,content_type,headers)
            if path == "/api/multimodel/frame":
                body,content_type,headers=self.service.multimodel_frame(self._one(query,"run"),self._one(query,"product"),self._one(query,"region","brazil"),self._one(query,"fh","0"),self._one(query,"stat","median"))
                return ApiResponse(200,body,content_type,headers)
            if path == "/api/multimodel/probability":
                try:threshold=float(self._one(query,"threshold"))
                except ValueError as exc:raise BadRequest("Limiar inválido.") from exc
                period_text=self._one(query,"period")
                try:period=int(period_text) if period_text else None
                except ValueError as exc:raise BadRequest("Período inválido.") from exc
                body,content_type,headers=self.service.probability(self._one(query,"run"),self._one(query,"variable"),self._one(query,"region","brazil"),self._one(query,"fh","0"),threshold,period)
                return ApiResponse(200,body,content_type,headers)
            return ApiResponse.json(404,{"error":"Rota de modelos inexistente.","code":"NOT_FOUND"})
        except ServiceError as exc:
            return ApiResponse.json(exc.status,{"error":str(exc),"code":exc.code})
        except (OSError,ValueError) as exc:
            return ApiResponse.json(400,{"error":str(exc),"code":"INVALID_MODEL_DATA"})
        except Exception:
            return ApiResponse.json(500,{"error":"Falha interna ao processar o produto meteorológico.","code":"MODEL_INTERNAL_ERROR"})

