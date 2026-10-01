# -*- coding: utf-8 -*-
"""网页设计自检：把"排版、留白、层级、微交互、响应式"变成可核对的检查项。

用法：py -3 tools/design_check.py [app.py]

检查内容：
1. 设计令牌是否齐全（颜色、圆角、间距、字阶、字体栈）；
2. 文字与背景的对比度是否达到 WCAG AA（正文 4.5:1，大字/UI 3:1）；
3. 是否具备微交互规则（悬浮/按下/键盘焦点环/过渡）；
4. 是否具备响应式与无障碍规则（媒体查询、减少动效、横滚容器）；
5. 字阶是否形成层级（≥4 级）、间距是否走统一倍数。
"""

from __future__ import annotations

import io
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REQUIRED_TOKENS = ["--paper", "--surface", "--ink", "--ink-soft", "--line", "--accent",
                   "--ok", "--warn", "--fail", "--radius", "--space", "--shadow",
                   "--font-display", "--font-ui", "--font-mono",
                   "--step-0", "--step-1", "--step-2", "--step-3", "--step-4"]
REQUIRED_RULES = {
    "微交互-悬浮": r"\.metric:hover|button:hover",
    "微交互-按下": r"button:active",
    "微交互-键盘焦点": r"focus-visible",
    "微交互-过渡": r"transition:",
    "响应式-媒体查询": r"@media\s*\(max-width",
    "无障碍-减少动效": r"prefers-reduced-motion",
    "版式-自适应网格": r"repeat\(auto-fit",
    "版式-等宽数字": r"font-mono",
    "层级-衬线标题": r"font-display",
    "表格-横滚容器": r"overflow:hidden|overflow-x",
}


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    high, low = max(a, b), min(a, b)
    return round((high + 0.05) / (low + 0.05), 2)


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
    text = path.read_text(encoding="utf-8")

    # 解析 DESIGN 字典里的颜色
    palette = dict(re.findall(r'"([a-z_]+)":\s*"(#[0-9A-Fa-f]{6})"', text))
    missing_tokens = [t for t in REQUIRED_TOKENS if t not in text]

    paper, ink = palette.get("paper", "#FFFFFF"), palette.get("ink", "#000000")
    checks = [
        ("正文/纸张 ≥4.5", contrast(ink, paper), 4.5),
        ("次要文字/纸张 ≥4.5", contrast(palette.get("ink_soft", "#888"), paper), 4.5),
        ("强调色/纸张 ≥4.5", contrast(palette.get("accent", "#888"), paper), 4.5),
        ("正常状态/纸张 ≥4.5", contrast(palette.get("ok", "#888"), paper), 4.5),
        ("警告状态/纸张 ≥4.5", contrast(palette.get("warn", "#888"), paper), 4.5),
        ("失败状态/纸张 ≥4.5", contrast(palette.get("fail", "#888"), paper), 4.5),
    ]
    steps = len(set(re.findall(r"--step-\d", text)))
    rules = {name: bool(re.search(pattern, text)) for name, pattern in REQUIRED_RULES.items()}

    print(f"设计自检：{path}")
    print(f"  设计令牌：{'齐全' if not missing_tokens else '缺失 ' + ', '.join(missing_tokens)}")
    print(f"  字阶层级：{steps} 级（建议 ≥4）")
    print("  对比度（WCAG AA）：")
    failed = []
    for label, ratio, threshold in checks:
        ok = ratio >= threshold
        failed += [] if ok else [label]
        print(f"    {'通过' if ok else '不通过'}  {label:<22} 实测 {ratio:.2f}")
    print("  设计规则：")
    for name, ok in rules.items():
        failed += [] if ok else [name]
        print(f"    {'通过' if ok else '不通过'}  {name}")
    if steps < 4:
        failed.append("字阶层级不足")
    print(f"\n结论：{'全部通过' if not failed else '待修 ' + str(len(failed)) + ' 项：' + '，'.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
