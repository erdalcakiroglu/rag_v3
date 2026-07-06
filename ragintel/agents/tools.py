"""FAZ 4 tool kayıt defteri: İP-3.0 retrieval servisleri LangGraph/LLM tool'u.

GÜVENLİK KONTRATI: `user_ctx` (ve `allowed_doc_scopes`) LLM'e AÇILMAZ — tool
JSON şemalarında YOKTUR. Yetki bağlamı `execute()` sırasında state'ten RUNTIME
enjekte edilir. Böylece LLM yetki kapsamını göremez/üretemez (yetki atlatma
önlenir). `test_faz4_tools_schema.py` şemada user_ctx olmadığını doğrular.
"""

from __future__ import annotations

from typing import Any, Callable

# --- LLM'e sunulan tool şemaları (OpenAI function-calling; user_ctx İÇERMEZ) --
_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "file_type": {"type": "string"},
        "language": {"type": "string"},
        "date_from": {"type": "string"},
        "date_to": {"type": "string"},
        "section": {"type": "string"},
    },
    "additionalProperties": False,
}

SEARCH_HYBRID_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_hybrid",
        "description": "Birincil retrieval: hibrit (dense+sparse RRF) arama. Soruyla ilgili chunk'ları getirir.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Arama sorgusu"},
                "top_k": {"type": "integer", "default": 10},
                "filters": _FILTERS_SCHEMA,
            },
            "required": ["query"],
        },
    },
}

LOOKUP_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_document",
        "description": "Bağlam genişletme: bir chunk'ın komşularını (devamını) getirir.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {"type": "integer"},
                "window": {"type": "integer", "default": 2},
            },
            "required": ["chunk_id"],
        },
    },
}

RERANK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rerank",
        "description": "10'dan fazla chunk varken alaka sırasını iyileştirir.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "chunk_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["query", "chunk_ids"],
        },
    },
}

MEMORY_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_search",
        "description": "Oturum hafızasından ilgili önceki mesajları getirir (MVP: stub).",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
}

# Terminal tool: agent yanıtı bununla teslim eder (tools node YÜRÜTMEZ; agent işler).
SUBMIT_ANSWER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_answer",
        "description": (
            "Nihai yanıtı teslim et. Yalnızca sağlanan bağlam bloklarındaki bilgiyle yanıtla; "
            "her iddiayı bir citation ile o bloktan birebir alıntıyla destekle."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "Markdown yanıt, inline [n] işaretli"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string", "description": "Yanıttaki iddia (cümle)"},
                            "chunk_id": {"type": "integer"},
                            "quote": {"type": "string", "description": "Chunk metninden birebir destekleyen ifade"},
                        },
                        "required": ["claim", "chunk_id", "quote"],
                    },
                },
            },
            "required": ["answer", "citations"],
        },
    },
}

SUBMIT_ANSWER = "submit_answer"


class ToolRegistry:
    """İP-3.0 RetrievalService'i LLM tool'larına bağlar. `service` None ise
    (graph flow testleri) retrieval tool'ları boş sonuç döndürür."""

    def __init__(self, service=None, *, include_memory: bool = True):
        self.service = service
        self._impls: dict[str, Callable[[dict, dict], dict]] = {
            "search_hybrid": self._search_hybrid,
            "lookup_document": self._lookup_document,
            "rerank": self._rerank,
        }
        self._schemas = [SEARCH_HYBRID_SCHEMA, LOOKUP_DOCUMENT_SCHEMA, RERANK_SCHEMA]
        if include_memory:
            self._impls["memory_search"] = self._memory_search
            self._schemas.append(MEMORY_SEARCH_SCHEMA)

    # -- LLM'e sunulan şemalar --------------------------------------------------
    def llm_tool_schemas(self) -> list[dict]:
        """Retrieval tool'ları + submit_answer (user_ctx YOK)."""
        return [*self._schemas, SUBMIT_ANSWER_SCHEMA]

    def final_only_schemas(self) -> list[dict]:
        """Bütçe bitince: yalnızca submit_answer sunulur (yanıt zorlanır)."""
        return [SUBMIT_ANSWER_SCHEMA]

    def executable_names(self) -> set[str]:
        return set(self._impls)

    # -- Yürütme (user_ctx RUNTIME enjekte) ------------------------------------
    def execute(self, name: str, arguments: dict, *, user_ctx: dict) -> dict:
        if name == SUBMIT_ANSWER:
            raise ValueError("submit_answer terminaldir; agent node işler, tools node yürütmez.")
        impl = self._impls.get(name)
        if impl is None:
            return {"error": f"tool_error: bilinmeyen tool '{name}'"}
        return impl(arguments, user_ctx)

    # -- Tekil tool implementasyonları -----------------------------------------
    def _search_hybrid(self, args: dict, user_ctx: dict) -> dict:
        if self.service is None:
            return {"chunks": []}
        chunks = self.service.search_hybrid(
            args["query"], top_k=int(args.get("top_k", 10)), filters=args.get("filters"), user_ctx=user_ctx
        )
        return {"chunks": chunks}

    def _lookup_document(self, args: dict, user_ctx: dict) -> dict:
        if self.service is None:
            return {"chunks": []}
        chunks = self.service.lookup_document(
            chunk_id=int(args["chunk_id"]), window=int(args.get("window", 2)), user_ctx=user_ctx
        )
        return {"chunks": chunks}

    def _rerank(self, args: dict, user_ctx: dict) -> dict:
        if self.service is None:
            return {"ranking": []}
        ranking = self.service.rerank(args["query"], [int(c) for c in args.get("chunk_ids", [])], user_ctx=user_ctx)
        return {"ranking": ranking}

    def _memory_search(self, args: dict, user_ctx: dict) -> dict:
        # MVP stub — session memory İP-3.5/FAZ 5'te bağlanır.
        return {"memory": []}


def accumulated_chunks(tool_output: Any) -> list:
    """Tool çıktısından retrieved'e eklenecek chunk listesi (yoksa boş)."""
    if isinstance(tool_output, dict) and isinstance(tool_output.get("chunks"), list):
        return tool_output["chunks"]
    return []
