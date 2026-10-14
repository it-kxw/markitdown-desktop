# SPDX-License-Identifier: MIT
"""冒烟矩阵：源码/冻结 × 并发 1/2 四种组合，逐文件比对字符数。

为什么要这条矩阵：
- 源码模式与冻结模式走的是两套不同的启动路径（解释器 vs PyInstaller 引导程序），
  并发 1 与 2 走两种不同的派发节奏；任何一处不一致都说明丢了文件、拿了缓存或串了
  结果——而这在单次运行里看不出来。
- 它同时是 T19 CI 的入口脚本：先在本机跑通，CI 才不会变成「红了没人管」。

复用 `p1_verify.py` 的夹具清单与报告读取，不另抄一份（抄出来的第二份迟早与第一份
漂移，比如「random.bin 是故意的失败夹具」这条规则）。

用法（Windows cmd）：
    .venv\\Scripts\\python.exe scripts\\smoke_matrix.py
    .venv\\Scripts\\python.exe scripts\\smoke_matrix.py --entries frozen

产物：smoke-matrix.json（机器可读；退出码 1 表示有组合不一致）。

@author 康小汪【kxw】
@date 2026-10-14
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 直接运行脚本与 pytest 导入都要能用：把 scripts/ 放进 sys.path 后才能复用 p1_verify
sys.path.insert(0, str(Path(__file__).resolve().parent))

from p1_verify import EXE, ROOT, _fixture_files, _read_report  # noqa: E402

#: 默认报告路径（与 p1-verify.json 同级，方便一起看）
REPORT = ROOT / "smoke-matrix.json"
ENTRY_SOURCE = "source"
ENTRY_FROZEN = "frozen"
ENTRY_CHOICES = (ENTRY_SOURCE, ENTRY_FROZEN)
#: --entries 的第三个取值：两个入口都跑
ENTRIES_DEFAULT = "both"
#: 并发档位：与用户界面上能选的档位一致（1 等于串行，最容易暴露顺序问题）
CONCURRENCIES = (1, 2)
#: 单条组合的超时（13 个夹具实测几秒，留足冷启动与杀毒扫描的余量）
_TIMEOUT_S = 900.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数（单独抽出来，测试可以直接喂参数而不动 sys.argv）。

    Args:
        argv: 参数列表；None 表示读 sys.argv。

    Returns:
        argparse.Namespace: 含 entries / json / timeout 三个字段。
    """
    parser = argparse.ArgumentParser(
        prog="smoke_matrix",
        description="源码/冻结 × 并发 1/2 冒烟矩阵：逐文件比对正文字符数",
    )
    parser.add_argument(
        "--entries",
        choices=(*ENTRY_CHOICES, ENTRIES_DEFAULT),
        default=ENTRIES_DEFAULT,
        help="跑哪些入口：source=源码、frozen=冻结包、both=两者（默认）",
    )
    parser.add_argument("--json", default=str(REPORT), help="报告写入路径")
    parser.add_argument("--timeout", type=float, default=_TIMEOUT_S, help="单条组合的超时秒数")
    return parser.parse_args(argv)


def selected_entries(value: str) -> list[str]:
    """把 --entries 的取值展开成入口列表。

    Args:
        value: source / frozen / both。

    Returns:
        list[str]: 有序的入口列表。
    """
    return list(ENTRY_CHOICES) if value == ENTRIES_DEFAULT else [value]


def entry_command(kind: str) -> list[str]:
    """给出某个入口的命令前缀。

    冻结包不存在时**直接报错**：静默退回源码会让「四种组合」名不副实，而这条矩阵
    的价值全在于四种组合真的都跑了。

    Args:
        kind: ENTRY_SOURCE 或 ENTRY_FROZEN。

    Returns:
        list[str]: 命令前缀（后续再拼 --selftest 等参数）。

    Raises:
        FileNotFoundError: 冻结包尚未构建。
        ValueError: 未知入口类型。
    """
    if kind == ENTRY_FROZEN:
        if not EXE.is_file():
            raise FileNotFoundError(f"冻结包不存在：{EXE}（先跑 scripts\\build_installer.ps1）")
        return [str(EXE)]
    if kind == ENTRY_SOURCE:
        return [sys.executable, "-m", "markitdown_desktop"]
    raise ValueError(f"未知入口：{kind}")


def combo_key(kind: str, concurrency: int) -> str:
    """组合标识，出现在报告与差异信息里（如 source/c1）。"""
    return f"{kind}/c{concurrency}"


def run_combo(kind: str, concurrency: int, workdir: Path, timeout: float) -> dict:
    """跑一条组合：13 个夹具、pool 模式、离屏渲染，返回参与比对的字段。

    Args:
        kind: 入口类型。
        concurrency: 并发数。
        workdir: 存放本次 JSON 报告的临时目录。
        timeout: 子进程超时秒数。

    Returns:
        dict: 含 key / ok / chars（文件名 -> 字符数）/ total_s 等字段；
        报告读取失败时 ok=False 并带 error 说明（不抛异常，让矩阵继续跑完）。
    """
    report_path = workdir / f"{kind}-c{concurrency}.json"
    command = [
        *entry_command(kind),
        "--selftest",
        *_fixture_files(),
        "--offscreen",
        "--mode",
        "pool",
        "--concurrency",
        str(concurrency),
        "--json",
        str(report_path),
    ]
    started = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    combo = {
        "key": combo_key(kind, concurrency),
        "entry": kind,
        "concurrency": concurrency,
        "returncode": proc.returncode,
        "wall_s": round(time.perf_counter() - started, 3),
        "ok": False,
        "error": "",
        "frozen": kind == ENTRY_FROZEN,
        "total_s": 0.0,
        "chars": {},
    }
    try:
        payload = _read_report(report_path)
    except (OSError, ValueError, KeyError) as exc:
        combo["error"] = f"{type(exc).__name__}: {exc}（子进程退出码 {proc.returncode}）"
        return combo
    combo["ok"] = bool(payload["ok"])
    combo["frozen"] = bool(payload["frozen"])
    combo["total_s"] = payload["total_s"]
    combo["chars"] = {row["file"]: row["chars"] for row in payload["results"]}
    return combo


def compare_combos(combos: list[dict]) -> list[str]:
    """逐文件比对字符数，返回差异描述（空列表表示四种组合完全一致）。

    比对口径是**每个文件的字符数**，不是总耗时：耗时天然有抖动，字符数不会。

    Args:
        combos: run_combo 的结果列表，第一条作为基准。

    Returns:
        list[str]: 差异描述；空列表表示全绿。
    """
    problems: list[str] = []
    for combo in combos:
        if not combo["ok"]:
            problems.append(f"{combo['key']} 自身失败：{combo.get('error') or '有文件未成功转换'}")
    baseline = combos[0]
    for combo in combos[1:]:
        for name, chars in baseline["chars"].items():
            if name not in combo["chars"]:
                problems.append(f"{name} 在 {combo['key']} 缺失（{baseline['key']} 有）")
            elif combo["chars"][name] != chars:
                problems.append(
                    f"{name} 字符数不一致：{baseline['key']}={chars} vs "
                    f"{combo['key']}={combo['chars'][name]}"
                )
        for name in combo["chars"]:
            if name not in baseline["chars"]:
                problems.append(f"{name} 在 {baseline['key']} 缺失（{combo['key']} 有）")
    return problems


def build_report(combos: list[dict], mismatches: list[str], fixture_count: int) -> dict:
    """组装机器可读报告。

    Args:
        combos: 各组合的运行结果。
        mismatches: compare_combos 给出的差异。
        fixture_count: 本次使用的夹具数量。

    Returns:
        dict: 可直接 json.dumps 的报告对象。
    """
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fixtures": fixture_count,
        "entries": [combo["entry"] for combo in combos],
        "concurrencies": sorted({combo["concurrency"] for combo in combos}),
        "combos": combos,
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    """跑完整矩阵并写报告。

    Args:
        argv: 参数列表；None 表示读 sys.argv。

    Returns:
        int: 0 表示四种组合逐文件一致；1 表示有差异（或某条组合失败）。
    """
    args = parse_args(argv)
    fixtures = _fixture_files()
    combos: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="mdd-smoke-") as tmp:
        workdir = Path(tmp)
        for kind in selected_entries(args.entries):
            for concurrency in CONCURRENCIES:
                print(f"[矩阵] {combo_key(kind, concurrency)} …", flush=True)
                combos.append(run_combo(kind, concurrency, workdir, args.timeout))
    mismatches = compare_combos(combos)
    report = build_report(combos, mismatches, fixture_count=len(fixtures))
    Path(args.json).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    for combo in combos:
        print(
            f"  {combo['key']}: ok={combo['ok']} 文件={len(combo['chars'])} "
            f"total_s={combo['total_s']} wall_s={combo['wall_s']}"
        )
    if mismatches:
        print("不一致：")
        for problem in mismatches:
            print(f"  - {problem}")
        return 1
    print(f"PASS：{len(combos)} 条组合、{len(fixtures)} 个夹具逐文件字符数一致 -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
