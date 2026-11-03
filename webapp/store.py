#!/usr/bin/env python3
"""申论复盘 Web 版 — 持久层（SQLite，纯标准库）。

职责：账号（scrypt 密码 + token 登录态）、历史会话、学生画像记忆、
容量治理（截断/条数上限/按龄轮转/库体积兜底）。

并发约定：单文件小服务流量极低，所有操作走模块级可重入锁串行化。
"""

import hashlib
import os
import secrets
import shutil
import sqlite3
import threading
import time
import uuid

_LOCK = threading.RLock()
_CONN = None
DB_PATH = None

# ---- 容量治理参数（configure() 可被 .env 覆盖） ----------------------------
USER_CONV_CAP = 300
HISTORY_DAYS = 180
HISTORY_MAX_MB = 512
MIN_FREE_MB = 200
MAX_FIELD_BYTES = 200 * 1024
MEMORY_CAP = 16
TRUNC_MARK = "\n[内容过长已截断]"


class Full(Exception):
    """存储已满（体积兜底或磁盘不足），调用方转为可读错误。"""


def configure(days=None, max_mb=None):
    global HISTORY_DAYS, HISTORY_MAX_MB
    if days is not None:
        HISTORY_DAYS = days
    if max_mb is not None:
        HISTORY_MAX_MB = max_mb


def init(data_dir: str):
    """建目录、连接、建表。进程启动时调用一次。"""
    global _CONN, DB_PATH
    import pathlib
    pathlib.Path(data_dir).mkdir(parents=True, exist_ok=True)
    DB_PATH = str(pathlib.Path(data_dir) / "history.db")
    _CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.execute("PRAGMA journal_mode=WAL")
    _CONN.execute("PRAGMA foreign_keys=ON")
    _CONN.executescript(_SCHEMA)
    _CONN.commit()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  username   TEXT NOT NULL UNIQUE,
  pw_hash    TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id         TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  kind       TEXT NOT NULL,
  mode       TEXT,
  title      TEXT NOT NULL,
  question   TEXT,
  material   TEXT,
  reference  TEXT,
  answer     TEXT,
  output     TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS messages (
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  idx INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  PRIMARY KEY (conversation_id, idx)
);
CREATE TABLE IF NOT EXISTS memories (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  kind       TEXT NOT NULL,
  content    TEXT NOT NULL,
  weight     REAL NOT NULL DEFAULT 1.0,
  active     INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id, active);
"""

# ---- 通用工具 --------------------------------------------------------------

def clip(s: str, limit: int = MAX_FIELD_BYTES) -> str:
    """按字节截断，不切进多字节字符；超长加尾注。"""
    s = s or ""
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    cut = b[:limit].decode("utf-8", errors="ignore")
    return cut + TRUNC_MARK


# ---- 账号：密码与用户 -------------------------------------------------------

def hash_password(password: str) -> str:
    """格式：scrypt$N$r$p$salt_hex$hash_hex（参数随哈希存储，便于升级）。"""
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$%s$%s" % (salt.hex(), h.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                           n=int(n), r=int(r), p=int(p))
        return secrets.compare_digest(h, bytes.fromhex(hash_hex))
    except Exception:
        return False


def create_user(username: str, password: str) -> int:
    with _LOCK:
        try:
            cur = _CONN.execute(
                "INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                (username, hash_password(password), time.time()),
            )
            _CONN.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("用户名已被注册")


def get_user(user_id: int):
    with _LOCK:
        return _CONN.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def find_user(username: str):
    with _LOCK:
        return _CONN.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def verify_user(username: str, password: str):
    row = find_user(username)
    if row and verify_password(password, row["pw_hash"]):
        return row
    return None


# ---- 登录态 token ----------------------------------------------------------

_TOKEN_TTL = 30 * 86400


def create_token(user_id: int, ttl: int = _TOKEN_TTL) -> str:
    raw = secrets.token_urlsafe(32)
    th = hashlib.sha256(raw.encode()).hexdigest()
    with _LOCK:
        _CONN.execute(
            "INSERT INTO auth_tokens(token_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
            (th, user_id, time.time(), time.time() + ttl),
        )
        _CONN.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (time.time(),))
        _CONN.commit()
    return raw


def user_for_token(raw: str):
    """校验并滑动续期；失效返回 None。"""
    if not raw:
        return None
    th = hashlib.sha256(raw.encode()).hexdigest()
    with _LOCK:
        row = _CONN.execute("SELECT * FROM auth_tokens WHERE token_hash=?", (th,)).fetchone()
        if not row or row["expires_at"] < time.time():
            return None
        _CONN.execute("UPDATE auth_tokens SET expires_at=? WHERE token_hash=?",
                      (time.time() + _TOKEN_TTL, th))
        _CONN.commit()
        uid = row["user_id"]
    return get_user(uid)


def delete_token(raw: str) -> None:
    if not raw:
        return
    th = hashlib.sha256(raw.encode()).hexdigest()
    with _LOCK:
        _CONN.execute("DELETE FROM auth_tokens WHERE token_hash=?", (th,))
        _CONN.commit()


# ---- 历史会话 ---------------------------------------------------------------

def create_conversation(user_id: int, kind: str, title: str, question: str,
                        mode: str = None, material: str = "", reference: str = "",
                        answer: str = "", output: str = "") -> str:
    """解析/批改单轮记录。写入前做磁盘与体积检查，满了抛 store.Full。"""
    with _LOCK:
        if not disk_ok():
            raise Full("服务器存储空间不足，请稍后再试或清理历史记录")
        conv_id = uuid.uuid4().hex
        now = time.time()
        _CONN.execute(
            "INSERT INTO conversations(id, user_id, kind, mode, title, question,"
            " material, reference, answer, output, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (conv_id, user_id, kind, mode, clip(title, 200), clip(question),
             clip(material), clip(reference), clip(answer), clip(output), now, now),
        )
        _CONN.commit()
    enforce_limits()
    return conv_id


def append_message(conversation_id: str, role: str, content: str) -> None:
    """学习模式逐轮追加。超出单会话 200 轮时滚动删除最早消息。"""
    with _LOCK:
        row = _CONN.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        idx = row["n"] if row else 0
        _CONN.execute(
            "INSERT INTO messages(conversation_id, idx, role, content) VALUES(?,?,?,?)",
            (conversation_id, idx, role, clip(content)),
        )
        if idx >= 200:  # 滚动删除最早的 20 轮（40 条）
            _CONN.execute(
                "DELETE FROM messages WHERE conversation_id=? AND idx<?",
                (conversation_id, 40),
            )
            # 重排剩余消息，保证 idx 连续
            rows = _CONN.execute(
                "SELECT idx FROM messages WHERE conversation_id=? ORDER BY idx",
                (conversation_id,),
            ).fetchall()
            for new_i, r in enumerate(rows):
                _CONN.execute(
                    "UPDATE messages SET idx=? WHERE conversation_id=? AND idx=?",
                    (new_i, conversation_id, r["idx"]),
                )
        _CONN.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), conversation_id)
        )
        _CONN.commit()


def get_conversation(user_id: int, conv_id: str):
    with _LOCK:
        return _CONN.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id)
        ).fetchone()


def get_messages(conversation_id: str) -> list:
    with _LOCK:
        rows = _CONN.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY idx",
            (conversation_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def list_conversations(user_id: int, q: str = "", limit: int = 100) -> list:
    """时间倒序；q 非空时在 title/question/output/messages 里 LIKE 搜索。"""
    with _LOCK:
        params = [user_id]
        sql = "SELECT id, kind, mode, title, created_at, updated_at FROM conversations WHERE user_id=?"
        if q:
            sql += " AND (title LIKE ? OR question LIKE ? OR output LIKE ? OR EXISTS(" \
                   "SELECT 1 FROM messages m WHERE m.conversation_id=conversations.id" \
                   " AND m.content LIKE ?))"
            kw = "%" + q + "%"
            params += [kw, kw, kw, kw]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = _CONN.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete_conversation(user_id: int, conv_id: str) -> bool:
    with _LOCK:
        # 先删子表消息再删会话，否则外键约束失败
        _CONN.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        cur = _CONN.execute(
            "DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id)
        )
        _CONN.commit()
        return cur.rowcount > 0


def count_conversations(user_id: int) -> int:
    with _LOCK:
        row = _CONN.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["n"] if row else 0


# ---- 学生画像（长期记忆） ---------------------------------------------------

def list_memories(user_id: int) -> list:
    """活跃条目，重的（重犯次数多）排前面。"""
    with _LOCK:
        rows = _CONN.execute(
            "SELECT id, kind, content, weight, updated_at FROM memories"
            " WHERE user_id=? AND active=1 ORDER BY weight DESC, updated_at DESC LIMIT ?",
            (user_id, MEMORY_CAP),
        ).fetchall()
    return [dict(r) for r in rows]


def add_memory(user_id: int, kind: str, content: str) -> None:
    with _LOCK:
        _CONN.execute(
            "INSERT INTO memories(user_id, kind, content, weight, active, created_at, updated_at)"
            " VALUES(?,?,?,1,1,?,?)",
            (user_id, kind, clip(content, 300), time.time(), time.time()),
        )
        _CONN.commit()


def touch_memory(user_id: int, mem_id: int, content: str = None) -> None:
    """重犯：weight+1 并刷新内容与时间。"""
    with _LOCK:
        if content:
            _CONN.execute(
                "UPDATE memories SET weight=weight+1, content=?, updated_at=? WHERE id=? AND user_id=?",
                (clip(content, 300), time.time(), mem_id, user_id),
            )
        else:
            _CONN.execute(
                "UPDATE memories SET weight=weight+1, updated_at=? WHERE id=? AND user_id=?",
                (time.time(), mem_id, user_id),
            )
        _CONN.commit()


def drop_memory(user_id: int, mem_id: int) -> bool:
    """软删除（画像展示里学生手动删 / 提炼器 drop）。"""
    with _LOCK:
        cur = _CONN.execute(
            "UPDATE memories SET active=0 WHERE id=? AND user_id=?", (mem_id, user_id)
        )
        _CONN.commit()
        return cur.rowcount > 0


# ---- 容量治理：轮转与清理 ---------------------------------------------------

def disk_ok() -> bool:
    if not DB_PATH:
        return True
    usage = shutil.disk_usage(os.path.dirname(DB_PATH))
    return usage.free >= MIN_FREE_MB * 1024 * 1024


def db_size_mb() -> float:
    if not DB_PATH:
        return 0.0
    try:
        return os.path.getsize(DB_PATH) / 1024 / 1024
    except OSError:
        return 0.0


def enforce_limits() -> dict:
    """惰性清理：按龄轮转 → 每用户条数上限 → 体积兜底。返回清理摘要。"""
    summary = {"aged": 0, "capped": 0, "vacuumed": False}
    now = time.time()
    with _LOCK:
        # 1) 按龄（先删子表消息，再删会话，避免外键约束失败）
        if HISTORY_DAYS > 0:
            olds = _CONN.execute(
                "SELECT id FROM conversations WHERE created_at < ?",
                (now - HISTORY_DAYS * 86400,),
            ).fetchall()
            for old in olds:
                _CONN.execute("DELETE FROM messages WHERE conversation_id=?", (old["id"],))
                _CONN.execute("DELETE FROM conversations WHERE id=?", (old["id"],))
            summary["aged"] = len(olds)
        # 2) 每用户条数上限：删最旧
        for row in _CONN.execute(
            "SELECT user_id, COUNT(*) AS n FROM conversations GROUP BY user_id HAVING n > ?",
            (USER_CONV_CAP,),
        ).fetchall():
            extra = row["n"] - USER_CONV_CAP
            olds = _CONN.execute(
                "SELECT id FROM conversations WHERE user_id=? ORDER BY created_at ASC LIMIT ?",
                (row["user_id"], extra),
            ).fetchall()
            for old in olds:
                _CONN.execute("DELETE FROM messages WHERE conversation_id=?", (old["id"],))
            _CONN.execute(
                "DELETE FROM conversations WHERE user_id=? AND id IN (%s)"
                % ",".join("?" * len(olds)),
                [row["user_id"]] + [o["id"] for o in olds],
            )
            summary["capped"] += len(olds)
        _CONN.commit()
        # 3) 体积兜底：删最旧 10% 再 VACUUM
        if db_size_mb() > HISTORY_MAX_MB:
            total = _CONN.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
            if total > 50:
                n_del = max(10, total // 10)
                olds = _CONN.execute(
                    "SELECT id FROM conversations ORDER BY created_at ASC LIMIT ?", (n_del,)
                ).fetchall()
                for old in olds:
                    _CONN.execute("DELETE FROM messages WHERE conversation_id=?", (old["id"],))
                _CONN.execute(
                    "DELETE FROM conversations WHERE id IN (%s)" % ",".join("?" * len(olds)),
                    [o["id"] for o in olds],
                )
            _CONN.commit()
            _CONN.execute("VACUUM")
            summary["vacuumed"] = True
    return summary


def vacuum() -> float:
    """手动空间回收，返回回收后库大小（MB）。"""
    with _LOCK:
        _CONN.execute("VACUUM")
    return db_size_mb()


def delete_account(user_id: int) -> None:
    """注销账号：级联删除该用户全部数据（先子后父，避免外键约束）。"""
    with _LOCK:
        convs = _CONN.execute(
            "SELECT id FROM conversations WHERE user_id=?", (user_id,)
        ).fetchall()
        for c in convs:
            _CONN.execute("DELETE FROM messages WHERE conversation_id=?", (c["id"],))
        _CONN.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))
        _CONN.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
        _CONN.execute("DELETE FROM auth_tokens WHERE user_id=?", (user_id,))
        _CONN.execute("DELETE FROM users WHERE id=?", (user_id,))
        _CONN.commit()
