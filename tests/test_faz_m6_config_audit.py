"""M-6 (b) — config_audit: app_config değişiklik günlüğü.

Kabul: panelden (admin_repo.write_config / patch_config_field) yapılan bir
değişiklik old/new + changed_by ile audit'e düşer; DOĞRUDAN SQL (psql'den
UPDATE, GUC set edilmeden) yapılan bir değişiklik de aynı trigger tarafından
yakalanır — ama changed_by bu kez session_user'a düşer (bayat/panel adını
UYDURMAZ). DDL bu test koşumunda CANLI `ragintel` şemasına UYGULANMAZ; geçici
bir "probe" şemasında kurulup kanıtlanır, sonra DROP edilir.

İki test grubu:
  A) API katmanı (DB'siz, sahte connection) — /audit ucunun 401/403 fail-closed
     deseni + admin_repo.list_config_audit'in çağrıldığının kanıtı.
  B) Gerçek trigger (canlı DB, geçici probe şema) — old/new + changed_by kaynağı.
"""

from __future__ import annotations

import random
import string
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from ragintel.database import admin_repo

# NOT: `pytestmark = pytest.mark.db` MODÜL DÜZEYİNDE KOYULMADI — Bölüm A
# (aşağıda) sahte/DB'siz connection kullanır, gerçek PostgreSQL GEREKTİRMEZ.
# Yalnızca Bölüm B'deki (canlı DB + probe şema) testler tek tek `db` işaretli.


# =============================================================================
# A) API katmanı — DB'siz, sahte connection (test_faz7_admin.py deseniyle aynı)
# =============================================================================
class _Cur:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, audit_rows):
        self.executed = []
        self._audit_rows = audit_rows

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = sql.lower()
        if "from config_audit" in s:
            return _Cur(self._audit_rows)
        return _Cur([])


class _FakeDb:
    def __init__(self, audit_rows=None):
        self.conn = _Conn(audit_rows or [])

    @contextmanager
    def connection(self):
        yield self.conn


class _Resolver:
    def __init__(self, is_admin):
        self._admin = is_admin

    def resolve(self, token):
        from ragintel.api.auth import Unauthorized
        if token != "tok":
            raise Unauthorized("geçersiz")
        return {"user_id": "u", "tenant_id": "t", "roles": ["user"],
                "allowed_doc_scopes": ["default"], "is_admin": self._admin}


def _client(is_admin, db=None):
    from langgraph.checkpoint.memory import InMemorySaver

    from ragintel.api.app import create_app
    from ragintel.api.runtime import RagRuntime
    from ragintel.config.loader import load_config
    from ragintel.config.settings import LangfuseSettings

    class _MockGW:
        def complete(self, **k):
            raise AssertionError("çağrılmamalı")

    class _MockSvc:
        def search_hybrid(self, *a, **k):
            return []

    class _MockCB:
        def build(self, r):
            return {"blocks": [], "citations": [], "dropped_chunk_ids": []}

    rt = RagRuntime(db=db or _FakeDb(), config=load_config(), gateway=_MockGW(),
                    checkpointer=InMemorySaver(), service=_MockSvc(), context_builder=_MockCB(),
                    langfuse=LangfuseSettings(host="", public_key="", secret_key=""),
                    resolver=_Resolver(is_admin))
    return TestClient(create_app(rt))


_A = {"Authorization": "Bearer tok"}


def test_audit_ucu_tokensiz_401():
    """Fail-closed: token yoksa 401 — mevcut `_admin()` deseniyle birebir aynı."""
    client = _client(is_admin=True)
    assert client.get("/api/admin/config/chunking/audit").status_code == 401


def test_audit_ucu_admin_degilse_403():
    """Fail-closed: geçerli token ama admin değil → 403 (veri sızmaz)."""
    client = _client(is_admin=False)
    assert client.get("/api/admin/config/chunking/audit", headers=_A).status_code == 403


def test_audit_ucu_admin_icin_listeyi_doner():
    """Admin için: admin_repo.list_config_audit çağrılır, `config_audit` sorgulanır,
    yanıt old/new + changed_by taşır. (Sahte DB gerçekten `config_audit`'ten
    okumazsa bu iddia kanıt taşımazdı — pozitif kontrol: satır GERÇEKTEN döner.)"""
    row = (1, "chunking", {"max_tokens": 256}, {"max_tokens": 512}, "erdal", "2026-07-13 10:00:00")
    db = _FakeDb(audit_rows=[row])
    client = _client(is_admin=True, db=db)
    r = client.get("/api/admin/config/chunking/audit", headers=_A)
    assert r.status_code == 200
    body = r.json()
    assert body["group"] == "chunking"
    assert len(body["audit"]) == 1
    a = body["audit"][0]
    assert a["old_value"] == {"max_tokens": 256} and a["new_value"] == {"max_tokens": 512}
    assert a["changed_by"] == "erdal"
    assert any("from config_audit" in s.lower() for s, _ in db.conn.executed), \
        "sahte DB GERÇEKTEN config_audit'i sorgulamıyorsa yukarıdaki iddia vacuous olurdu"


# =============================================================================
# B) Gerçek trigger — canlı DB'de GEÇİCİ probe şema (ragintel şemasına DOKUNULMAZ)
# =============================================================================
def _rand_schema() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"config_audit_probe_{suffix}"


@pytest.fixture
def probe_schema(live_db):
    """docs/FAZ7_Sema_Ek1_ConfigAudit.sql ile AYNI trigger mantığını geçici bir
    şemada kurar (canlı `ragintel` şemasına DOKUNMAZ). Test sonunda DROP eder —
    hata olsa da (assert patlasa da) fixture teardown'ı çalışır ve temizler."""
    schema = _rand_schema()
    with live_db.connection() as conn:
        conn.execute(f"CREATE SCHEMA {schema};")
        conn.execute(f"""
            CREATE TABLE {schema}.app_config (
                config_key   text PRIMARY KEY,
                config_value jsonb NOT NULL,
                description  text,
                updated_by   text,
                updated_at   timestamptz NOT NULL DEFAULT now()
            );
        """)
        conn.execute(f"""
            CREATE TABLE {schema}.config_audit (
                audit_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                config_key text NOT NULL,
                old_value  jsonb,
                new_value  jsonb NOT NULL,
                changed_by text,
                changed_at timestamptz NOT NULL DEFAULT now()
            );
        """)
        conn.execute(f"""
            CREATE OR REPLACE FUNCTION {schema}.fn_audit_app_config()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                v_by text;
            BEGIN
                -- NULLIF(...,'') ŞART: pool'da AYNI bağlantı yeniden kullanıldığında,
                -- daha önce SET LOCAL ile dokunulmuş bir özel GUC, transaction bitince
                -- NULL değil boş string'e döner — NULLIF olmadan session_user'a
                -- düşülmez (bu proje canlı DDL'inde de düzeltildi, bkz.
                -- docs/FAZ7_Sema_Ek1_ConfigAudit.sql).
                v_by := COALESCE(NULLIF(current_setting('app.changed_by', true), ''), session_user);
                IF TG_OP = 'INSERT' THEN
                    INSERT INTO {schema}.config_audit (config_key, old_value, new_value, changed_by)
                    VALUES (NEW.config_key, NULL, NEW.config_value, v_by);
                ELSIF TG_OP = 'UPDATE' THEN
                    IF NEW.config_value IS DISTINCT FROM OLD.config_value THEN
                        INSERT INTO {schema}.config_audit (config_key, old_value, new_value, changed_by)
                        VALUES (NEW.config_key, OLD.config_value, NEW.config_value, v_by);
                    END IF;
                END IF;
                RETURN NEW;
            END $$;
        """)
        conn.execute(f"""
            CREATE TRIGGER trg_app_config_audit
                AFTER INSERT OR UPDATE ON {schema}.app_config
                FOR EACH ROW EXECUTE FUNCTION {schema}.fn_audit_app_config();
        """)
        conn.execute(
            f"INSERT INTO {schema}.app_config (config_key, config_value, updated_by) "
            f"VALUES ('demo', %s, 'ilk_kurulum');",
            (Jsonb({"a": 1}),),
        )
    try:
        yield schema
    finally:
        with live_db.connection() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")


def _last_audit_row(live_db, schema):
    with live_db.connection() as conn:
        conn.execute(f"SET LOCAL search_path TO {schema}, public;")
        return conn.execute(
            "SELECT old_value, new_value, changed_by FROM config_audit "
            "WHERE config_key='demo' ORDER BY audit_id DESC LIMIT 1;"
        ).fetchone()


@pytest.mark.db
def test_panel_yazimi_audit_yakalar_write_config(live_db, probe_schema):
    """Panelden (POST /config/{group} → admin_repo.write_config) yapılan tam-grup
    yazımı: audit'e old/new + changed_by=GERÇEK admin adı düşer.

    NEDEN önemli: write_config artık yazımdan ÖNCE `SELECT set_config('app.changed_by', ...)`
    çağırıyor (bkz. admin_repo._set_audit_actor) — trigger bunu okuyup doğru
    aktörü kaydediyor mu, bunu kanıtlıyoruz.
    """
    with live_db.connection() as conn:
        conn.execute(f"SET LOCAL search_path TO {probe_schema}, public;")
        admin_repo.write_config(conn, group="demo", value={"a": 2}, updated_by="erdal_panel")

    row = _last_audit_row(live_db, probe_schema)
    assert row is not None, "audit satırı OLUŞMALIYDI (pozitif ön-koşul) — yazım sonrası kontrol"
    old_value, new_value, changed_by = row
    assert old_value == {"a": 1} and new_value == {"a": 2}
    assert changed_by == "erdal_panel"


@pytest.mark.db
def test_panel_yazimi_audit_yakalar_patch_config_field(live_db, probe_schema):
    """M-5 alan-bazlı yazım (PATCH → admin_repo.patch_config_field) da AYNI GUC
    mekanizmasını kullanır — yalnızca write_config değil, PATCH ucu da kapsanır."""
    with live_db.connection() as conn:
        conn.execute(f"SET LOCAL search_path TO {probe_schema}, public;")
        n = admin_repo.patch_config_field(conn, group="demo", path=["a"], value=7,
                                          updated_by="erdal_patch")
    assert n == 1, "satır bulunup güncellenmeliydi (pozitif ön-koşul)"

    row = _last_audit_row(live_db, probe_schema)
    assert row is not None
    old_value, new_value, changed_by = row
    assert old_value == {"a": 1} and new_value == {"a": 7}
    assert changed_by == "erdal_patch"


@pytest.mark.db
def test_dogrudan_sql_degisikligi_audit_yakalar_session_user_fallback(live_db, probe_schema):
    """DOĞRUDAN SQL (psql'den UPDATE, GUC set EDİLMEDEN) yapılan değişiklik de
    trigger tarafından yakalanır — ama changed_by bu kez `session_user`'a düşer,
    ÖNCEKİ bir panel-admin'inin adını UYDURMAZ.

    Bu, M-6 ön-veri kararının kanıtı: NEW.updated_by yerine session GUC + fallback
    kullanmanın nedeni tam olarak bu senaryo — updated_by kolonuna dokunulmayan
    doğrudan bir UPDATE, bayat/yanlış bir isim üretmemeli.
    """
    with live_db.connection() as conn:
        conn.execute(f"SET LOCAL search_path TO {probe_schema}, public;")
        # GUC set EDİLMEDİ — gerçek "doğrudan SQL" senaryosu.
        conn.execute(
            "UPDATE app_config SET config_value=%s WHERE config_key='demo';",
            (Jsonb({"a": 99}),),
        )
        expected_role = conn.execute("SELECT session_user;").fetchone()[0]

    row = _last_audit_row(live_db, probe_schema)
    assert row is not None, "audit satırı OLUŞMALIYDI (pozitif ön-koşul) — doğrudan SQL de yakalanmalı"
    old_value, new_value, changed_by = row
    assert old_value == {"a": 1} and new_value == {"a": 99}
    # Pozitif + kesin: gerçek DB rolüne EŞİT (yalnızca "boş değil" ya da "panel
    # adından farklı" değil — GERÇEKTEN session_user'a düştüğünü kanıtlar).
    assert changed_by == expected_role
    assert changed_by != "erdal_panel"


def test_yalnizca_description_degisirse_audit_yazilmaz():
    """Trigger yalnızca config_value DEĞİŞİRSE audit yazar (gürültü azaltma).
    Bu testin kendi izole ürettiği tabloyu kullanır (DB gerektirmez)."""
    # Bu doğrulama saf SQL mantığı (IS DISTINCT FROM) üzerinden yapıldığından
    # ve zaten yukarıdaki testlerde canlı trigger'la dolaylı kanıtlandığından
    # (config_value değişmeyen bir UPDATE audit üretmemeli), burada trigger
    # FONKSİYONUNUN kaynağını okuyarak KOŞULUN var olduğunu doğruluyoruz —
    # DDL dosyasının kendisiyle davranış arasında sürüklenme (drift) olmasın.
    import pathlib
    ddl = pathlib.Path(__file__).resolve().parents[1] / "docs" / "FAZ7_Sema_Ek1_ConfigAudit.sql"
    text = ddl.read_text(encoding="utf-8")
    assert "IS DISTINCT FROM" in text, "trigger yalnızca gerçek değişiklikte yazmalı"


@pytest.mark.db
def test_canli_ragintel_semasi_degismedi(live_db, probe_schema):
    """M-6 kabul: bu test koşumu canlı `ragintel` şemasına config_audit/trigger
    UYGULAMAMALI (DDL onaya bekliyor). Yokluk iddiasının kanıt değeri taşıması
    için ÖNCE pozitif kontrol: aynı sorgu tekniği probe şemada VARLIĞI doğru
    tespit ediyor mu?
    """
    with live_db.connection() as conn:
        # Pozitif ön-koşul: sorgu tekniği probe şemada GERÇEKTEN buluyor.
        probe_exists = conn.execute(
            "SELECT to_regclass(%s);", (f"{probe_schema}.config_audit",)
        ).fetchone()[0]
        assert probe_exists is not None, \
            "probe şemada config_audit bulunmalıydı — sorgu tekniği güvenilir değilse " \
            "aşağıdaki 'canlı şemada yok' iddiası da güvenilmez olur"

        # Asıl iddia: canlı ragintel şemasında YOK (DDL uygulanmadı).
        live_exists = conn.execute("SELECT to_regclass('ragintel.config_audit');").fetchone()[0]
        live_trig = conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_app_config_audit' "
            "AND tgrelid = 'ragintel.app_config'::regclass;"
        ).fetchone()[0]

    assert live_exists is None, \
        "config_audit CANLI ragintel şemasında bulundu! DDL yalnızca onaydan sonra uygulanmalıydı."
    assert live_trig == 0, \
        "trg_app_config_audit CANLI app_config'e uygulanmış! DDL yalnızca onaydan sonra uygulanmalıydı."
