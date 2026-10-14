# -*- coding: utf-8 -*-
"""AA 汉化矫正工作台：FastAPI 服务入口（2026-10-14 自单文件拆出）。

启动：双击仓库根 一键重启工作台.bat，或 python scripts/tools/aa_web.py
（自动打开浏览器 http://127.0.0.1:8765）

本文件只做两件事：① 取底座里的 app（uvicorn 入口 `main:app` 靠它）
② 按域挂载 routers/*。路由实现见各模块；共享状态/工具见 web_state.py。
"""
from aa_web_app.web_state import app                     # noqa: F401  uvicorn 入口
from aa_web_app.routers import (
    admin,
    board,
    chapters,
    deliver,
    replace,
    settings,
    site_url,
    tasks,
    terms,
    works,
)

for _router_mod in (admin.router, board.router, chapters.router, deliver.router, replace.router, settings.router, site_url.router, tasks.router, terms.router, works.router):
    app.include_router(_router_mod)


# ── 兼容层（2026-10-14 拆分时加）─────────────────────────────────────────────
# 拆分前这些名字都挂在 main 上；仓库里 15 个用例文件按 `aa_web_app.main.X` / `M.X` /
# `self.M.X` 访问（AST 枚举，含私有名）。此处**显式**转发一次，保持可 grep；
# **新代码请直接 import 所属模块**（底座 web_state / 各域 routers.*）。
# 注意：`from m import f` 绑定的是副本 —— 用例侧 mock.patch 必须打在**调用方模块**，
# 或模块限定的 web_state 上（cfg_of / invalidate_stats 已改成 `web_state.X` 调用）。
from aa_web_app.web_state import (  # noqa: F401
    HTTPException,
    TM,
    add_work,
    cfg_of,
    logic,
    registry,
)
from aa_web_app.routers.board import (  # noqa: F401
    _board_enqueue,
    _board_index_meta,
    _task_public,
)
from aa_web_app.routers.chapters import (  # noqa: F401
    LineSave,
    _log_edit,
    _log_edits_bulk,
    api_line_get,
    api_line_html,
    api_save_line,
)
from aa_web_app.routers.replace import (  # noqa: F401
    _norm_manual_pairs,
    _plan_writes,
)
from aa_web_app.routers.settings import (  # noqa: F401
    SettingsReq,
    api_settings,
    api_settings_save,
    api_settings_test_key,
)
from aa_web_app.routers.site_url import (  # noqa: F401
    api_work_sync_wiki,
    api_work_update,
)
from aa_web_app.routers.tasks import (  # noqa: F401
    _chain_env,
    api_translate_start,
    api_work_models,
)
from aa_web_app.routers.terms import (  # noqa: F401
    RepatriateBody,
    WorkGlossary,
    api_candidate_confirm,
    api_candidate_delete,
    api_candidate_whitelist,
    api_candidates,
    api_candidates_promote_work_glossary,
    api_repatriate_apply,
    api_repatriate_list,
    api_work_glossary_put,
)

__all__ = ["app"]
