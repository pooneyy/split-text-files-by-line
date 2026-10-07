"""
核心模块

- 输入: list[Path]  输入文件按列表顺序拼接
- 方案: list[int]  依次取每个块大小，切分方案的最后一个值作为之后所有块的最大值
- 命名: {prefix}{index:0Nd}{suffix}, N 根据 plan_total 决定
- 冲突: skip / overwrite / rename (-1, -2, ...)
- 换行输出: preserve / lf / crlf

本模块同时通过 ``from version import __version__`` 再导出 ``__version__``，
方便 CLI/测试等场景 ``python -c "import core; print(core.__version__)"`` 拿到
当前版本。
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Sequence, Tuple

from version import __version__  # noqa: F401  (re-exported)

# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class SplitConfig:
    encoding: str = "utf-8"
    newline_policy: str = "preserve"  # preserve | lf | crlf
    conflict_policy: str = "rename"   # skip | overwrite | rename
    prefix: str = ""
    suffix: str = ".txt"
    index_start: int = 1

@dataclass
class OutputResult:
    written_files: List[str]
    skipped: List[str]
    errors: List[str]

# --------------------------------------------------------------------------- #
# 计数 / 预估
# --------------------------------------------------------------------------- #

def count_lines(path: Path, encoding: str) -> int:
    """快速预扫行数。整个文件按 \n 计数"""
    with open(path, "rb") as f:
        data = f.read()

    text = data.decode(encoding, errors="replace")
    if not text:
        return 0

    return len(text.splitlines())

def total_lines(paths: Sequence[Path], encoding: str) -> int:
    return sum(count_lines(p, encoding) for p in paths)

def estimate_outputs(total: int, plan: Sequence[int]) -> int:
    """根据方案预估输出文件数（与 run_split 行为一致）。

    方案列表的最后一个值作为之后所有文件的方案（重复使用）。
    """
    if not plan:
        return 1 if total > 0 else 0
    for size in plan:
        if size <= 0:
            raise ValueError(f"切分行数必须为正整数，得到 {size}")
    if total <= 0:
        return 0
    last = plan[-1]
    remaining = total
    # 先减去 plan 中前 n-1 个明确值
    for size in plan[:-1]:
        remaining -= size
    if remaining <= 0:
        return len(plan[:-1])  # 没有剩余，不需要最后一个/尾巴
    # 剩余按 last 切
    full = remaining // last
    rem = remaining - full * last
    count = len(plan) - 1 + full
    if rem > 0:
        count += 1
    return count

def decide_padding(plan_total: int) -> int:
    """根据预计输出文件数决定序号 zero-pad 位数。最少 1。"""
    if plan_total <= 0:
        return 1
    digits = len(str(plan_total))
    return max(1, digits)

# --------------------------------------------------------------------------- #
# 命名 / 冲突
# --------------------------------------------------------------------------- #

def make_filename(index: int, cfg: SplitConfig, padding: int) -> str:
    return f"{cfg.prefix}{index:0{padding}d}{cfg.suffix}"

def resolve_conflict(path: Path, policy: str) -> Tuple[str, str]:
    """返回 (final_path_str, mode)。

    mode: 'skip' | 'overwrite' | 'rename'
    """
    if not os.path.exists(path):
        return (str(path), "new")
    if policy == "skip":
        return (str(path), "skip")
    if policy == "overwrite":
        return (str(path), "overwrite")
    # rename: foo.txt -> foo_1.txt, foo_2.txt, ...
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return (str(candidate), "rename")
        i += 1

# --------------------------------------------------------------------------- #
# 切分执行
# --------------------------------------------------------------------------- #

def _output_newline(policy: str) -> str:
    if policy == "lf":
        return "\n"
    if policy == "crlf":
        return "\r\n"
    return os.linesep  # preserve -> 使用系统默认

def iter_input_lines(
    paths: Sequence[Path], encoding: str
) -> Iterator[Tuple[int, str]]:
    """按顺序产出 (global_line_no, line)。不含行尾换行符。

    global_line_no 从 1 开始。
    若任一文件解码失败，抛出 UnicodeDecodeError。
    """
    gno = 0
    for p in paths:
        with open(p, "r", encoding=encoding, newline="") as f:
            for raw in f:
                # raw 包含可能的 \n / \r\n / 不含（最后一行）
                # splitlines 的语义里这些行是同一行
                gno += 1
                line = raw.rstrip("\n").rstrip("\r")
                yield gno, line

def run_split(
    paths: Sequence[Path],
    plan: Sequence[int],
    cfg: SplitConfig,
    output_dir: Path,
    log: Callable[[str], None] = lambda _msg: None,
    progress: Callable[[int, int], None] = lambda _done, _total: None,
    should_cancel: Callable[[], bool] = lambda: False,
) -> OutputResult:
    """主入口: 合并 -> 按方案切 -> 命名 -> 写文件。"""
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    # 预估总文件数（用于决定 padding 与日志）
    total = total_lines(paths, cfg.encoding)
    plan_total = estimate_outputs(total, plan)
    padding = decide_padding(plan_total)

    nl = _output_newline(cfg.newline_policy)

    # 状态
    remaining_in_current = plan[0] if plan else 0
    plan_idx = 0
    file_index = cfg.index_start
    current_outfile = None
    current_out_path: str | None = None
    current_out_mode: str = "new"
    lines_in_current = 0

    def close_current() -> None:
        nonlocal current_outfile, current_out_path, current_out_mode
        if current_outfile is not None:
            try:
                current_outfile.close()
            except OSError as e:
                if current_out_path:
                    errors.append(f"关闭失败 {current_out_path}: {e}")
        if current_out_path is not None:
            if current_out_mode == "skip":
                skipped.append(current_out_path)
                log(f"[跳过] {current_out_path}")
            elif current_out_mode in ("new", "overwrite", "rename"):
                written.append(current_out_path)
                log(
                    f"[{('覆盖' if current_out_mode == 'overwrite' else '写入')}] "
                    f"{current_out_path}"
                )
        current_outfile = None
        current_out_path = None
        current_out_mode = "new"

    def open_next() -> bool:
        """为下一个 part 打开输出文件。返回是否成功。"""
        nonlocal current_outfile, current_out_path, current_out_mode
        nonlocal remaining_in_current, plan_idx, file_index, lines_in_current
        name = make_filename(file_index, cfg, padding)
        candidate = target_dir / name
        final_str, mode = resolve_conflict(candidate, cfg.conflict_policy)
        if mode == "skip":
            # 跳过这个文件: 仍要让索引推进，并消耗掉对应行数
            current_outfile = None
            current_out_path = final_str
            current_out_mode = "skip"
            skipped.append(final_str)
            log(f"[跳过] {final_str}")
            return False
        # 写模式
        try:
            current_outfile = open(final_str, "w", encoding=cfg.encoding, newline="")
        except OSError as e:
            errors.append(f"无法创建 {final_str}: {e}")
            log(f"[错误] 无法创建 {final_str}: {e}")
            current_outfile = None
            current_out_path = final_str
            current_out_mode = "skip"
            return False
        current_out_path = final_str
        current_out_mode = mode
        lines_in_current = 0
        return True

    # 启动第一个 part
    if total > 0:
        file_index = cfg.index_start
        plan_idx = 0
        remaining_in_current = plan[0] if plan else 0
        open_next()

    line_iter = iter_input_lines(paths, cfg.encoding)
    done = 0
    try:
        for gno, line in line_iter:
            if should_cancel():
                log("[取消] 用户中止")
                break

            # 决定是否要开新 part（current_outfile 为 None 也需要重开，例如 skip 之后）
            need_new = (
                (current_outfile is None and current_out_path is not None)
                or current_outfile is not None
            ) and lines_in_current >= remaining_in_current

            if need_new:
                if current_outfile is not None:
                    close_current()
                file_index += 1
                plan_idx += 1
                # 之后所有文件都用方案列表的最后一个值作为每段行数
                if plan:
                    remaining_in_current = plan[min(plan_idx, len(plan) - 1)]
                else:
                    remaining_in_current = 10**18
                open_next()

            done = gno
            progress(done, total)

            if current_outfile is not None:
                try:
                    current_outfile.write(line + nl)
                except OSError as e:
                    if current_out_path:
                        errors.append(f"写入失败 {current_out_path}: {e}")
                    log(f"[错误] 写入失败 {current_out_path}: {e}")
                    close_current()
                    file_index += 1
                    open_next()
                else:
                    lines_in_current += 1
            else:
                # 当前 part 被跳过
                lines_in_current += 1

        progress(total, total)
    finally:
        close_current()

    return OutputResult(written_files=written, skipped=skipped, errors=errors)
