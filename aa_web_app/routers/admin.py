# -*- coding: utf-8 -*-
"""服务级运维路由：热加载（清缓存 + 刷首页快照）、打开作品文件夹。"""
import os
import registry
from fastapi import HTTPException
from aa_web_app import web_state
from aa_web_app import logic
from aa_web_app.web_state import (_INDEX_SNAP, _LASTUPD_CACHE, _R18_SUMMARY_CACHE, _STATS_CACHE, app)
from aa_web_app.routers.site_url import _index_refresh_async
from fastapi import APIRouter

router = APIRouter()


# ---------- 热加载 ----------


@router.post("/api/reload")
def api_reload(warm: bool = True, clear_cache: bool = True):
    """热加载 registry（新作品/代码调整无需重启服务）。

    - importlib.reload(registry)：新作品、改动的 registry 条目立即生效
      （main.py 全部经 `registry.WORKS` 模块属性访问，reload 后自动指向新 dict）
    - 清空内存统计缓存（_STATS_CACHE），下次访问按磁盘增量缓存重算
    - sites 解析器是运行时 importlib.import_module（见 check_chapter 调用点），天然读新代码
    - warm=False（删除作品路径）：不启动全量预热线程——删除后前端立即 reload，
      预热全量冷扫（75 部 × ~25s）会与首页请求抢磁盘 IO/GIL 导致首页卡刷新。
    - clear_cache=False（删除路径）：不清其它作品的统计缓存（删单部不影响其余），
      只由调用方 pop 被删作品，避免 reload 后首页逐部冷重算。
    """
    # reload registry 前先确保其依赖 paths 也新鲜（registry 里 `from paths import BASE`）
    import importlib as _il
    try:
        _paths = _il.import_module("paths")
        _il.reload(_paths)
    except ImportError:
        pass
    _il.reload(registry)
    # 清空统计缓存：作品可能增删，旧缓存条目失效；删除路径可保留其余缓存
    if clear_cache:
        _STATS_CACHE.clear()
        logic.invalidate_mch_cache()
        _R18_SUMMARY_CACHE.clear()
        _LASTUPD_CACHE.clear()
    # 后台重建首页快照（新作品/删除的作品不阻塞响应）；删除路径 warm=False 跳过防抢 IO
    _INDEX_SNAP.invalidate(None)          # 作品集合变了 → 整份快照重算
    if warm:
        _index_refresh_async(force=True)
    return {"ok": True, "works": len(registry.WORKS)}


# ---------- 打开作品文件夹（2026-09-05） ----------


@router.post("/api/open-folder/{work}")
def api_open_folder(work: str):
    """在资源管理器中打开该作品根目录（aa/汉化/raw/work 的上级，含 override/glossary 等）。

    纯本地工具：服务端 os.startfile 弹出文件夹，前端无 file:// 限制问题。
    """
    cfg = web_state.cfg_of(work)
    root = os.path.dirname(cfg["aa_dir"])
    if not os.path.isdir(root):
        raise HTTPException(404, f"作品目录不存在: {root}")
    try:
        os.startfile(root)          # Windows 打开资源管理器
    except Exception as e:
        raise HTTPException(500, f"打开失败: {e}")
    return {"ok": True, "path": root}
