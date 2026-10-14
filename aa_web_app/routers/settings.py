# -*- coding: utf-8 -*-
"""配置域：设置页（密钥/模型/划词/代理）、划词翻译、TTS 朗读。"""
import os
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from aa_web_app.web_state import (app, templates)
from fastapi import APIRouter

router = APIRouter()


# ---------- 划词翻译 + 朗读 TTS（2026-09-03，selection.py） ----------


class SelectionReq(BaseModel):
    text: str = ""
    work: str = ""      # 空 = 无作品上下文（board 页裸翻）
    base: str = ""      # 章节 base(entry_<eid>)，有则按章注入术语表/知识库
    force: bool = False  # true = 「重译」按钮：跳过照抄判定强制出结果 + 强化指令
    avoid: str = ""      # 上次通道名（force 重译时排到最后，避免同一模型给同样结果）


@router.post("/api/translate-selection")
def api_translate_selection(body: SelectionReq):
    """划词日→中：付费三通道(deepseek-flash → deepinfra → runinfra)；术语注入与整章翻译同口径。

    返回 {ok, text, via, elapsed, echo}——echo=true 表示模型把原文照抄回来了（前端显示黄色提示）。
    """
    from aa_web_app import selection as _SEL
    if not _SEL.sel_enabled():
        raise HTTPException(403, "划词翻译已在「设置」里关闭（AA_SEL_ENABLE=0）")
    try:
        res = _SEL.translate_text(body.text, work=body.work or None,
                                  base=body.base or None,
                                  force=bool(body.force), avoid=body.avoid or None)
        return {"ok": True, **res}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except SystemExit as e:
        # 2026-09-08: 脚本库按设计 raise SystemExit(如术语表硬前置)逃逸会杀死 uvicorn 进程
        # (except Exception/Starlette 中间件都接不住 BaseException)——请求路径一律转 500 保命。
        raise HTTPException(500, f"内部终止: {e}")
    except Exception as e:
        raise HTTPException(502, f"翻译失败: {e}")


@router.post("/api/tts")
def api_tts(body: SelectionReq):
    """朗读原文（edge-tts ja-JP-NanamiNeural）。返回音频路径（FileResponse 由调用方决定）。"""
    from aa_web_app import selection as _SEL
    if not _SEL.sel_enabled():
        raise HTTPException(403, "朗读已在「设置」里关闭（AA_SEL_ENABLE=0）")
    try:
        path = _SEL.tts_path(body.text)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))
    return FileResponse(path, media_type="audio/mpeg",
                        filename=os.path.basename(path))


# ---------- 设置页（2026-10-08 阶段 6；方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md）----------


class SettingsReq(BaseModel):
    section: str = ""                 # keys | models | selection | proxy
    set: dict = {}                    # 密钥：{KEY: 新值}（只提交改动项）
    clear: list = []                  # 密钥：显式清除的 key 名
    translate: list = []              # 模型：翻译线条目 id 列表（空 = 整条链）
    proofread: list = []              # 模型：校对线条目 id
    sel_chain: list = []              # 划词：划词链条目 id
    sel_enabled: bool = True          # 划词开关
    proxy_url: str = ""               # 代理地址（空 = 不改）
    proxy_mode: str = ""              # auto | 0 | 1（空 = 不改）
    clear_url: bool = False           # 删掉 .env 里的 AA_PROXY（回 TOML/默认）
    name: str = ""                    # test-key 用


def _settings_mod():
    from aa_web_app import settings as _S
    return _S


@router.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"active": "settings"})


@router.get("/api/settings")
def api_settings():
    """当前配置（密钥只回掩码 + 来源层，不回明文）。"""
    try:
        return _settings_mod().state()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/settings")
def api_settings_save(body: SettingsReq):
    """按区块保存（只写改动项）。"""
    S = _settings_mod()
    try:
        if body.section == "keys":
            r = S.save_keys(body.set, body.clear)
        elif body.section == "models":
            r = S.save_models(translate=body.translate or [], proofread=body.proofread or [])
        elif body.section == "selection":
            S.save_selection(bool(body.sel_enabled))
            r = S.save_models(selection=body.sel_chain or [])
            r["sel_enabled"] = S.sel_enabled()
        elif body.section == "proxy":
            r = S.save_proxy(body.proxy_url, body.proxy_mode, clear_url=bool(body.clear_url))
        else:
            raise HTTPException(422, "未知区块: %s" % body.section)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    out = S.state()
    out["changed"] = r.get("changed", [])
    return out


@router.post("/api/settings/test-key")
def api_settings_test_key(body: SettingsReq):
    """「测试连接」：用该 key 发一次最小请求（零成本量级）。"""
    try:
        return _settings_mod().test_key(body.name)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/settings/test-proxy")
def api_settings_test_proxy():
    """「测试代理」：① 代理端口可连？② 不走代理/经代理能否到付费端点。"""
    try:
        return _settings_mod().test_proxy()
    except Exception as e:
        return {"ok": False, "error": str(e)}
