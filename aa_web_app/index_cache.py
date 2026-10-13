# -*- coding: utf-8 -*-
"""首页数据快照缓存（2026-10-13，方案 A）。

**为什么**：`/` 的作品列表要扫全库——每个作品的汉化目录 + 每章 `work/prep/*.json`
+ 各类 `work/*.json`，实测 109 部作品 ≈ 1.2 万个小文件、`stats_cache` 4.8MB + `prep` 20.1MB。
F 盘是 SATA HDD：冷盘（重启电脑后）几分钟、常驻进程里 TTL 过期也要 8s。

**做法**：把 `main._build_index_data()` 的结果落盘成一份 json（`aa_web_app/data/index_cache.json`），
请求路径只读快照 → 秒回；重建交给后台线程（限流 + 单飞），显式改动（保存行/重生成/术语替换/
增删作品）走 `invalidate()` 局部重算。

**职责边界**：本模块只管「快照存取 + 脏标记 + 限流单飞判定 + 陈旧文案」，**不知道报告数据怎么算**
（由调用方传结果/回调），因此不 import fastapi/main，可单测。
"""
import json
import os
import threading
import time

SNAPSHOT_VERSION = 1
DEFAULT_MAX_AGE = 600        # 快照超过该秒数 → 触发后台重建（请求路径仍用旧值，不阻塞）
DEFAULT_MIN_INTERVAL = 60    # 两次后台重建之间的最小间隔（防抖）


def snapshot_path(data_root):
    """快照文件路径（数据根，随 AA_BASE 走）。"""
    return os.path.join(data_root, "aa_web_app", "data", "index_cache.json")


def age_text(seconds):
    """「数据更新于 X」用的相对时间文案。"""
    if seconds is None:
        return "未知"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return "%d 分钟前" % (seconds // 60)
    if seconds < 86400:
        return "%d 小时前" % (seconds // 3600)
    return "%d 天前" % (seconds // 86400)


def wrap(data):
    """给报告数据加版本与时间戳。"""
    snap = dict(data or {})
    snap["v"] = SNAPSHOT_VERSION
    snap["built_at"] = time.time()
    return snap


class IndexSnapshot:
    """首页快照的进程内状态 + 磁盘持久化。

    用法（见 main.index）：
        snap = store.get()                 # None = 还没建过
        ...                                # 请求路径只读 snap
        store.set(_build_index_data())     # 重建后写内存 + 磁盘
        store.should_refresh() → begin_refresh() → 后台重建 → set() → end_refresh()
    """

    def __init__(self, path, max_age=DEFAULT_MAX_AGE, min_interval=DEFAULT_MIN_INTERVAL):
        self.path = path
        self.max_age = max_age
        self.min_interval = min_interval
        self._lock = threading.RLock()
        self._snap = None            # dict 或 None（None 且 _loaded 为真 = 磁盘上也没有）
        self._loaded = False
        self._dirty = set()          # 需重算的作品；持久化进快照的 "dirty"
        self._refreshing = False     # 单飞：后台重建进行中
        self._last_start = 0.0       # 上次重建开始（限流用）
        self._last_error = None

    # ---------- 读 ----------

    def load_disk(self):
        """从磁盘读快照；损坏/版本不符/不存在 → None。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or d.get("v") != SNAPSHOT_VERSION:
            return None
        if not isinstance(d.get("works"), list):
            return None
        return d

    def get(self):
        """内存快照；首次调用时尝试读盘（读不到返回 None，由调用方同步重建）。"""
        with self._lock:
            if not self._loaded:
                self._loaded = True
                disk = self.load_disk()
                if disk is not None:
                    self._snap = disk
                    for w in (disk.get("dirty") or []):
                        self._dirty.add(w)
            return self._snap

    def age(self, now=None):
        """快照年龄（秒）；无快照 → None。"""
        with self._lock:
            if not self._snap:
                return None
            return max(0.0, (now if now is not None else time.time())
                       - float(self._snap.get("built_at") or 0))

    def age_text_now(self):
        a = self.age()
        return "未建立" if a is None else age_text(a)

    # ---------- 写 ----------

    def set(self, data):
        """写入内存 + 原子落盘。data 应为 wrap() 过的完整报告数据。"""
        with self._lock:
            snap = dict(data or {})
            snap["v"] = SNAPSHOT_VERSION
            snap.setdefault("built_at", time.time())
            snap["dirty"] = sorted(self._dirty)
            self._snap = snap
            self._loaded = True
        self._write(snap)
        return snap

    def _write(self, snap):
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def clear(self):
        """丢掉内存快照（下次 get() 会重新读盘）。"""
        with self._lock:
            self._snap = None
            self._loaded = False

    # ---------- 脏标记 ----------

    def invalidate(self, work=None):
        """标脏：work=None → 全部重算；否则只重算该作品。

        只改内存（不落盘）——保存行/术语替换这类高频路径不该每次重写整份快照；
        脏集合会在 `set()`/`clear_dirty()` 时随快照一起落盘。
        """
        with self._lock:
            if work is None:
                self._dirty.add("*")
            else:
                self._dirty.add(work)
            if self._snap is not None:
                self._snap["dirty"] = sorted(self._dirty)

    def dirty(self):
        with self._lock:
            if not self._loaded:
                self.get()          # 触发磁盘加载（快照里的 dirty 段要一起恢复）
            return set(self._dirty)

    def needs_full_rebuild(self):
        with self._lock:
            return "*" in self._dirty

    def clear_dirty(self, works=None):
        """清脏标记（重建成功后调用）。works=None → 全清。落盘交给随后的 set()。"""
        with self._lock:
            if works is None:
                self._dirty.clear()
            else:
                for w in works:
                    self._dirty.discard(w)
            if self._snap is not None:
                self._snap["dirty"] = sorted(self._dirty)

    # ---------- 后台重建的限流与单飞 ----------

    def should_refresh(self, now=None):
        """是否需要（后台）重建：没快照、快照过期、或有待重算的脏作品。"""
        now = now if now is not None else time.time()
        with self._lock:
            if self._snap is None:
                return True
            if self._dirty:
                return True
            return (now - float(self._snap.get("built_at") or 0)) >= self.max_age

    def begin_refresh(self, force=False, now=None):
        """抢占一次后台重建。返回 False = 已有在跑 / 距上次太近（限流）。"""
        now = now if now is not None else time.time()
        with self._lock:
            if self._refreshing:
                return False
            if not force and (now - self._last_start) < self.min_interval:
                return False
            self._refreshing = True
            self._last_start = now
            return True

    def end_refresh(self, error=None):
        with self._lock:
            self._refreshing = False
            self._last_error = error

    @property
    def refreshing(self):
        with self._lock:
            return self._refreshing

    @property
    def last_error(self):
        with self._lock:
            return self._last_error
