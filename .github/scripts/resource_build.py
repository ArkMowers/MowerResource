#!/usr/bin/env python3
"""MowerResource 资源包生成管线（在 GitHub Actions 内运行）。

子命令:
  check  判断本次调度是否该出包：易变源 default branch HEAD 有变。
         需要出包 → exit 0；否则 exit 1。
  build  拉 5 源 → 跑生成脚本(auto_get_res_new.py) → 包内容无变化则跳过发布；
         有变化则打 zip、发 GitHub Release、提交 version.json + 状态到 main。
  hotupdate  生成后筛 stage_data_full 的 ACTIVITY 子集写成 stage_data.json，
         连同 key_mapping.json 推送到 MowerHotUpdate main（无变化跳过，幂等）。

运行环境（由 workflow 提供）:
  GITHUB_WORKSPACE       MowerResource 检出（仓库根 = 发布目标）
  GITHUB_WORKSPACE/mower fork 检出（生成脚本 + arknights_mower 包）
  GITHUB_EVENT_NAME      schedule / workflow_dispatch
  GITHUB_TOKEN           脚本走 GitHub API
  GH_TOKEN               gh CLI（建 Release / 传资产 / 清旧）
  MOWERFONTS_SSH_KEY     MowerFonts 只读 deploy key
  HOTUPDATE_SSH_KEY      MowerHotUpdate 只写 deploy key（推活动关卡 overlay）
"""

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

STATE_FILE = "source_state.json"  # 管线内部状态（易变源 sha + 已发布内容哈希）

# 易变源（游戏数据）；只有它们 default branch HEAD 变了才重打
VOLATILE_SOURCES = [
    "Kengxxiao/ArknightsGameData",  # gamedata/excel
    "yuanyan3060/ArknightsGameResource",  # item + avatar
]

KEEP_RELEASES = 5  # 只保留最近 N 个 Release（客户端只要最新）
RELEASE_ASSET = "resource.zip"  # 资产名稳定，客户端 releases/latest/download 拉
VERSION_JSON = "arknights_mower/data/version.json"  # 生成脚本产出的版本元数据
HOTUPDATE_REPO = "ArkMowers/MowerHotUpdate"  # 热更仓库（只推文件、不建 Release）
HOTUPDATE_CLONE_URL = f"git@github.com:{HOTUPDATE_REPO}.git"
STAGE_OVERLAY_FILE = "stage_data.json"  # 热更活动关卡文件（ACTIVITY 子集）
KEY_MAPPING_FILE = "key_mapping.json"  # 物品名映射，供热更仓库打包时解析掉落中文名


def workspace() -> Path:
    return Path(os.environ["GITHUB_WORKSPACE"])


def mower_dir() -> Path:
    return workspace() / "mower"


def load_res_version():
    """加载 fork 检出的 res_version.py（文件集/展示版本等纯逻辑，单一来源）。"""
    src = mower_dir() / "arknights_mower/utils/res_version.py"
    spec = importlib.util.spec_from_file_location("mower_res_version", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_package_spec():
    mod = load_res_version()
    return mod.RES_PACKAGE_DIRS, mod.RES_PACKAGE_MODELS, mod.RES_PACKAGE_DATA


def work_dir() -> Path:
    base = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    return base / "resbuild"


def run(cmd, check=True, capture=False):
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败 {cmd}: {result.stdout} {result.stderr}")
    return result.stdout if capture else result


def git_run(cmd: list, env: dict | None = None) -> None:
    """跑 git 命令；失败时把 stderr 带进异常，无人值守 CI 便于定位。"""
    result = subprocess.run(cmd, capture_output=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(f"git 失败: {detail or cmd}")


def api_json(url: str):
    req = urllib.request.Request(url)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    req.add_header("User-Agent", "mower-resource-builder")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def default_branch(repo: str) -> str:
    return api_json(f"https://api.github.com/repos/{repo}")["default_branch"]


def repo_head(repo: str) -> str:
    branch = default_branch(repo)
    return api_json(f"https://api.github.com/repos/{repo}/commits/{branch}")["sha"]


def load_state() -> dict:
    path = workspace() / STATE_FILE
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    path = workspace() / STATE_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cmd_check() -> int:
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print("手动 dispatch，强制出包")
        return 0
    heads = {repo: repo_head(repo) for repo in VOLATILE_SOURCES}
    state = load_state()
    changed = any(state.get("sources", {}).get(r) != sha for r, sha in heads.items())
    if not changed:
        print("易变源 default branch 无变化，跳过")
        return 1
    print("易变源有变化，出包")
    return 0


def sparse_clone(url: str, branch: str, dest: Path, subdirs: list) -> None:
    """稀疏克隆单个目录子树，避免整仓下载。"""
    shutil.rmtree(dest, ignore_errors=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--branch",
            branch,
            url,
            str(dest),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "sparse-checkout", "set", *subdirs],
        check=True,
    )


def copy_tree(src: Path, dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def ssh_git_env(key_env: str, key_name: str) -> dict:
    """把 GitHub secret 私钥写盘，返回带 GIT_SSH_COMMAND 的环境（供 git clone/push）。

    secret 支持两种存法：原始 PEM（含字面 \\n 时还原），或 base64（无换行，最稳，
    推荐）。写盘按字节，不 UTF-8 解码（OpenSSH 私钥 body 是二进制，解码会炸）。
    """
    key = os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"{key_env} 未设置")
    key = key.replace("\\n", "\n").strip()
    if "-----BEGIN" in key:
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = base64.b64decode(key)
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(exist_ok=True)
    key_file = ssh_dir / key_name
    with open(key_file, "wb") as f:
        f.write(key_bytes + b"\n")
    os.chmod(key_file, 0o600)
    env = dict(os.environ)
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_file} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
    )
    return env


def fetch_fonts() -> Path:
    """克隆私有 MowerFonts（走 MOWERFONTS_SSH_KEY），返回 fonts 目录。"""
    env = ssh_git_env("MOWERFONTS_SSH_KEY", "mowerfonts_key")
    fonts = work_dir() / "mowerfonts"
    shutil.rmtree(fonts, ignore_errors=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "git@github.com:ArkMowers/MowerFonts.git",
            str(fonts),
        ],
        env=env,
        check=True,
    )
    return fonts / "fonts"


def fetch_sources() -> Path:
    """拉 5 源进 fork 检出的期望路径，返回 MowerFonts 的 fonts 目录。"""
    mower = mower_dir()
    res_root = mower / "ArknightsGameResource"
    work = work_dir()

    # 1. excel：Kengxxiao/ArknightsGameData 的 zh_CN/gamedata/excel
    excel = work / "excel"
    sparse_clone(
        "https://github.com/Kengxxiao/ArknightsGameData.git",
        default_branch("Kengxxiao/ArknightsGameData"),
        excel,
        ["zh_CN/gamedata/excel"],
    )
    copy_tree(excel / "zh_CN/gamedata/excel", res_root / "gamedata/excel")

    # 2+3. item + avatar：yuanyan3060/ArknightsGameResource
    res = work / "res"
    sparse_clone(
        "https://github.com/yuanyan3060/ArknightsGameResource.git",
        default_branch("yuanyan3060/ArknightsGameResource"),
        res,
        ["item", "avatar"],
    )
    copy_tree(res / "item", res_root / "item")
    copy_tree(res / "avatar", res_root / "avatar")
    # avatar 上游只保留 char_*（剔 trap_/token_/npc_ 等非玩家干员）
    for f in (res_root / "avatar").glob("*"):
        if f.is_file() and not f.name.startswith("char_"):
            f.unlink()

    # 4. composite_table：Arknights-yituliu/frontend-v2-plus dev
    composite_dest = mower / "frontend-v2-plus-dev/src/static/json/material"
    composite_dest.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(
        "https://raw.githubusercontent.com/Arknights-yituliu/frontend-v2-plus/dev/"
        "src/static/json/material/composite_table.v2.json",
        composite_dest / "composite_table.v2.json",
    )

    # 5. 上游 version 文件（yuanyan3060 仓库根时间戳）→ last_updated 用
    urllib.request.urlretrieve(
        "https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/main/version",
        res_root / "version",
    )

    # 6. fonts：ArkMowers/MowerFonts（私有，deploy key）
    return fetch_fonts()


def run_generation(fonts_dir: Path) -> None:
    env = dict(os.environ)
    env["MOWERFONTS_DIR"] = str(fonts_dir)
    subprocess.run(
        [sys.executable, "auto_get_res_new.py"], cwd=mower_dir(), env=env, check=True
    )


def read_version_info() -> dict:
    """读生成脚本产出的 version.json 全文。"""
    with open(mower_dir() / VERSION_JSON, encoding="utf-8") as f:
        return json.load(f)


def read_res_version() -> tuple:
    """从 version.json 取 (res_version, 内容哈希)。"""
    info = read_version_info()
    res_version = info.get("res_version", "")
    content_hash = res_version.rsplit("-", 1)[1] if "-" in res_version else ""
    return res_version, content_hash


def build_release_notes(version_info: dict) -> str:
    """生成 Release 说明：展示版本 + res_version + 包内容 + 安装 + 版权。"""
    mod = load_res_version()
    display = mod.display_version(version_info)
    activity = version_info.get("activity") or {}
    gacha = version_info.get("gacha") or {}
    last_updated = version_info.get("last_updated", "")
    lines = [
        "## 资源版本",
        "",
        f"- **展示版本**：{display or '未知'}",
        f"- **res_version**：{version_info.get('res_version', '')}",
    ]
    if last_updated:
        lines.append(f"- **数据快照**：{last_updated}")
    if activity.get("name"):
        lines.append(f"- **最新活动**：{activity['name']}")
    if gacha.get("name"):
        lines.append(f"- **当前卡池**：{gacha['name']}")
    lines += [
        "",
        "## 包内容",
        "",
        "游戏数据整包（webp / pkl / json），解压覆盖到 mower 安装目录即可生效：",
        "",
        "- `ui/public/depot/`、`ui/public/avatar/`、`ui/public/building_skill/`",
        "- `arknights_mower/models/`",
        "- `arknights_mower/data/`",
        "",
        "## 安装",
        "",
        "- **应用内**：mower 自动检测更新后下载应用",
        "- **手动**：QQ 群拖拽 `resource.zip` 到设置页应用",
        "",
        "## 版权",
        "",
        "游戏素材 ©上海鹰角网络科技有限公司，仅用于学习与交流，侵删。",
    ]
    return "\n".join(lines)


def build_zip(out_zip: Path) -> None:
    """把资源包文件集（含 version.json）打成 zip，条目用源文件真实 mtime。"""
    mower = mower_dir()
    dirs, models, data = load_package_spec()
    rels = []
    for rel in dirs:
        d = mower / rel
        if d.is_dir():
            rels.extend(p.relative_to(mower) for p in d.rglob("*") if p.is_file())
    for rel in models + data:
        p = mower / rel
        if p.is_file():
            rels.append(p.relative_to(mower))
    rels.append(Path(VERSION_JSON))
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(rels, key=str):
            st = (mower / rel).stat()
            zi = zipfile.ZipInfo(
                rel.as_posix(), date_time=time.localtime(st.st_mtime)[:6]
            )
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(mower / rel, "rb") as fh:
                zf.writestr(zi, fh.read())


def ensure_release(tag: str, asset: Path, version_info: dict) -> None:
    """创建/覆盖对应 tag 的 Release 并上传资源包 zip（带说明）。"""
    run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"], check=False)
    notes_file = work_dir() / "release_notes.md"
    notes_file.parent.mkdir(parents=True, exist_ok=True)
    notes_file.write_text(build_release_notes(version_info), encoding="utf-8")
    run(
        [
            "gh",
            "release",
            "create",
            tag,
            str(asset),
            "--target",
            "main",
            "--title",
            f"资源包 {tag}",
            "--notes-file",
            str(notes_file),
        ],
        check=True,
    )


def prune_releases() -> None:
    """删除旧 Release（保留最近 KEEP_RELEASES 个）。"""
    out = run(
        ["gh", "release", "list", "--limit", "100", "--json", "tagName"],
        check=True,
        capture=True,
    )
    tags = [r["tagName"] for r in json.loads(out)]
    for tag in tags[KEEP_RELEASES:]:
        run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"], check=True)
        print(f"清理旧 Release {tag}")


def commit_and_push(files: list, message: str) -> None:
    ws = workspace()
    git = ["git", "-C", str(ws)]
    subprocess.run([*git, "add", *files], check=True)
    if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode == 0:
        print("无文件变更，跳过提交")
        return
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            message,
        ],
        check=True,
    )
    subprocess.run([*git, "push", "origin", "HEAD:main"], check=True)


def cmd_build() -> int:
    fonts = fetch_sources()
    run_generation(fonts)
    res_version, content_hash = read_res_version()

    state = load_state()
    state["sources"] = {repo: repo_head(repo) for repo in VOLATILE_SOURCES}
    if content_hash and content_hash == state.get("content_hash"):
        print("包内容无变化，跳过发布")
        save_state(state)
        commit_and_push([STATE_FILE], "chore: 记录已见源状态（内容无变化）")
        return 0

    out_zip = work_dir() / RELEASE_ASSET
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    build_zip(out_zip)
    ensure_release(res_version, out_zip, read_version_info())
    prune_releases()

    copy_file(mower_dir() / VERSION_JSON, workspace() / "version.json")
    state["content_hash"] = content_hash
    save_state(state)
    commit_and_push(["version.json", STATE_FILE], f"build: 资源包更新 {res_version}")
    print(f"已发布资源包 {res_version}")
    return 0


def stage_overlay_bytes() -> bytes:
    """从生成脚本产出的 stage_data_full.json 筛 ACTIVITY 子集（条目原样，endTs 嵌套原样保留）。"""
    src = mower_dir() / "arknights_mower/data/stage_data_full.json"
    if not src.exists():
        raise RuntimeError(f"缺少生成产物 {src}，需先跑 build")
    with open(src, encoding="utf-8") as f:
        full = json.load(f)
    overlay = [x for x in full if x.get("stageType") == "ACTIVITY"]
    # 与生成脚本同款序列化（ensure_ascii=False + indent=2），保证字节稳定可幂等比较
    return json.dumps(overlay, ensure_ascii=False, indent=2).encode("utf-8")


def key_mapping_bytes() -> bytes:
    """生成脚本产出的物品名映射，供热更仓库打包 Release notes 解析掉落中文名。"""
    src = mower_dir() / "arknights_mower/data/key_mapping.json"
    if not src.exists():
        raise RuntimeError(f"缺少生成产物 {src}")
    return src.read_bytes()


def push_hotupdate_overlay() -> None:
    """推热更数据文件（活动关卡 overlay + 物品名映射）到 MowerHotUpdate main；无变化跳过。"""
    env = ssh_git_env("HOTUPDATE_SSH_KEY", "hotupdate_key")
    files = {
        STAGE_OVERLAY_FILE: stage_overlay_bytes(),
        KEY_MAPPING_FILE: key_mapping_bytes(),
    }
    repo = work_dir() / "mowerhotupdate"
    shutil.rmtree(repo, ignore_errors=True)
    git_run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            HOTUPDATE_CLONE_URL,
            str(repo),
        ],
        env=env,
    )
    git = ["git", "-C", str(repo)]
    changed = []
    for name, content in files.items():
        target = repo / name
        if target.exists() and target.read_bytes() == content:
            continue
        target.write_bytes(content)
        git_run([*git, "add", name])
        changed.append(name)
    if not changed:
        print("热更数据无变化，跳过推送")
        return
    if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode == 0:
        print("热更数据无变化，跳过提交")
        return
    git_run(
        [
            *git,
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            "build(hotupdate): 更新热更数据\n\n"
            "活动开启/结束或物品表变化时随之更新，随资源包生成推送到热更仓库：\n"
            "活动关卡供客户端运行时按 id 覆盖基线活动关，物品名映射供 Release "
            "notes 解析掉落中文名。\n\n"
            "Refs: #171",
        ]
    )
    git_run([*git, "push", "origin", "HEAD:main"], env=env)
    print(f"已推送热更数据（{'、'.join(changed)}）到 {HOTUPDATE_REPO}")


def cmd_hotupdate() -> int:
    push_hotupdate_overlay()
    return 0


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 2
    sub = argv[0]
    if sub == "check":
        return cmd_check()
    if sub == "build":
        return cmd_build()
    if sub == "hotupdate":
        return cmd_hotupdate()
    print(f"未知子命令: {sub}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # 让 CI 步骤失败可见
        print(f"管线执行失败: {e}", file=sys.stderr)
        sys.exit(1)
