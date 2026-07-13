"""FastAPI uygulaması — /api/ask, /api/feedback, /api/health + tek sayfa UI.

Yanıt gövdesi Tasarim_FAZ4 §5 FinalResponse BİREBİR; session_id/injection
durumu HTTP header'larda taşınır (gövde §5-saf kalır).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .auth import Unauthorized, bearer_token
from .runtime import InputRejected, RagRuntime
from .schemas import AskRequest, FeedbackRequest, FinalResponse

_STATIC = Path(__file__).parent / "static"


def build_default_runtime() -> RagRuntime:
    """Üretim runtime'ı: DB + PostgresSaver + LiteLLM/Ollama gateway."""
    from langgraph.checkpoint.postgres import PostgresSaver

    from ..config.loader import load_config
    from ..config.settings import DbSettings, LiteLLMSettings
    from ..database import Database, make_db_reader
    from ..llm.gateway import LiteLLMGateway

    db = Database(DbSettings()).open()
    cfg = load_config(db_reader=make_db_reader(db))
    # Demo/provider-swap esnekliği: model/api_base OS env ile override edilebilir
    # (LiteLLMSettings .env-only olduğundan api_base'i açıkça geçiriyoruz).
    # Model önceliği: OS env RAGINTEL_AGENT_MODEL > .env RAGINTEL_LLM_MODEL > DB.
    llm = LiteLLMSettings()
    model = os.environ.get("RAGINTEL_AGENT_MODEL") or llm.model or cfg.group("agent").model
    api_base = os.environ.get("RAGINTEL_LLM_API_BASE") or llm.api_base
    req_timeout = float(os.environ.get("RAGINTEL_LLM_REQUEST_TIMEOUT") or llm.request_timeout)
    gateway = LiteLLMGateway(model=model, settings=LiteLLMSettings(api_base=api_base, request_timeout=req_timeout))
    cm = PostgresSaver.from_conn_string(DbSettings().conninfo())
    saver = cm.__enter__()
    rt = RagRuntime(db=db, config=cfg, gateway=gateway, checkpointer=saver)
    rt._cm, rt._db = cm, db  # shutdown için
    return rt


def create_app(runtime: RagRuntime | None = None) -> FastAPI:
    app = FastAPI(title="ragintel", version="0.1", description="RAG v2 — FAZ 7 öncü servis")
    state: dict = {"runtime": runtime}

    @app.on_event("startup")
    def _startup():
        if state["runtime"] is None:
            state["runtime"] = build_default_runtime()
        # Tokenizer'ı arka planda ön-ısıt: ilk kullanıcı ~15s soğuk yükleme
        # beklemesin. Bitene kadar /api/health "warming" döner.
        state["runtime"].warm_up_async()

    @app.on_event("shutdown")
    def _shutdown():
        rt = state["runtime"]
        cm = getattr(rt, "_cm", None)
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass

    def rt() -> RagRuntime:
        if state["runtime"] is None:
            raise HTTPException(503, "runtime hazır değil")
        return state["runtime"]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.post("/api/ask")
    def ask(req: AskRequest, response: Response, authorization: str | None = Header(default=None)):
        # FAZ 6: Authorization: Bearer <token> ZORUNLU (fail-closed). X-User-Id KALDIRILDI.
        try:
            res = rt().ask(req.question, req.session_id, bearer_token(authorization))
        except Unauthorized as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})
        except InputRejected as exc:
            raise HTTPException(400, str(exc))
        # §5 sözleşmesini doğrula/serialize et (drift olursa test yakalar).
        final = FinalResponse.model_validate(res.final_response)
        response.headers["X-Session-Id"] = res.session_id
        response.headers["X-Injection-Flagged"] = "1" if res.injection_flagged else "0"
        return JSONResponse(final.model_dump(), headers={
            "X-Session-Id": res.session_id,
            "X-Injection-Flagged": "1" if res.injection_flagged else "0",
        })

    @app.get("/api/table/{table_id}")
    def table(table_id: int, authorization: str | None = Header(default=None)):
        """M-2: kaynak panelindeki tablo-kökenli citation'ın yapısal gösterimi.

        GÜVENLİK (fail-closed): tablonun dosyası kullanıcının doc_scope'larında
        değilse 404 döner — 403 DEĞİL, çünkü 403 tablonun VAR OLDUĞUNU sızdırır.
        Var olmayan table_id ile ayırt edilemez yanıt.
        """
        try:
            user_ctx = rt().resolver.resolve(bearer_token(authorization))
        except Unauthorized as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})
        payload = rt().table(table_id, user_ctx)
        if payload is None:
            raise HTTPException(404, "Tablo bulunamadı")
        return payload

    @app.get("/api/figure/{figure_id}")
    def figure(figure_id: int, authorization: str | None = Header(default=None)):
        """M-7: kaynak panelindeki görselin PNG'si.

        GÜVENLİK (fail-closed, M-2 tablo ucuyla BİREBİR): görselin dosyası
        kullanıcının doc_scope'larında değilse 404 — 403 DEĞİL, çünkü 403 görselin
        VAR OLDUĞUNU sızdırır. Var olmayan figure_id, scope dışı görsel ve görüntüsü
        kaydedilmemiş kayıt AYIRT EDİLEMEZ yanıt verir.
        """
        try:
            user_ctx = rt().resolver.resolve(bearer_token(authorization))
        except Unauthorized as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})
        fig = rt().figure(figure_id, user_ctx)
        if fig is None:
            raise HTTPException(404, "Görsel bulunamadı")
        path = Path(fig["storage_path"])
        # DB'de yol var ama dosya diskte yoksa (depo taşındı/silindi): yine 404 —
        # 500 vermek iç dosya yolunu ve varlığını sızdırırdı.
        if not path.is_file():
            raise HTTPException(404, "Görsel bulunamadı")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/feedback")
    def feedback(req: FeedbackRequest):
        return rt().feedback(session_id=req.session_id, trace_id=req.trace_id,
                             rating=req.rating, comment=req.comment)

    @app.get("/api/health")
    def health():
        return rt().health()

    # FAZ 7 — Admin panel uçları (/admin sayfası + /api/admin/*, admin guard'lı).
    from .admin import register_admin_routes
    register_admin_routes(app, rt)

    return app
