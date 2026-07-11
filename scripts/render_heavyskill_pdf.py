#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs" / "heavyskill-reports" / "v2-p0-p1-acceptance-2026-07-11"
OUTPUT = REPORT_DIR / "v2-p0-p1-acceptance.pdf"


def main() -> None:
    pdfmetrics.registerFont(TTFont("HeitiCN", "/System/Library/Fonts/STHeiti Medium.ttc", subfontIndex=0))
    styles = getSampleStyleSheet()
    body = ParagraphStyle("BodyCN", parent=styles["BodyText"], fontName="HeitiCN", fontSize=10.5, leading=17, textColor=colors.HexColor("#20201e"), spaceAfter=8)
    title = ParagraphStyle("TitleCN", parent=body, fontSize=28, leading=35, alignment=TA_CENTER, spaceAfter=18)
    h1 = ParagraphStyle("H1CN", parent=body, fontSize=17, leading=23, textColor=colors.HexColor("#205caa"), spaceBefore=16, spaceAfter=9)
    h2 = ParagraphStyle("H2CN", parent=body, fontSize=13, leading=18, spaceBefore=12, spaceAfter=7)
    meta = ParagraphStyle("MetaCN", parent=body, alignment=TA_CENTER, fontSize=9, textColor=colors.HexColor("#66635e"))
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title="V2 P0-P1 深度验收报告", author="Codex HeavySkill")
    story = [Paragraph("MANUFACTURING GEO AUDIT · CODEX/V2 · 2026-07-11", meta), Spacer(1, 10), Paragraph("V2 P0-P1 深度验收报告", title), Paragraph("三个隔离审计视角共同指出：初始实现已有广度，但证据不足。修复后，可靠性核心已通过真实 PostgreSQL、Redis 与双 worker 演练，完整 HTTP、浏览器和可访问性套件也已在当前工作树成功通过。", body)]
    story += section("审计方法", ["采用产品/PRD、可靠性/架构、UX/QA 三个独立 trace，再由主流程重新推导结论。判断标准不是“没有发现错误”，而是每项需求必须存在范围匹配的运行证据。"], h1, body)
    story += section("本轮实质修复", [
        "• 项目切换保护未保存输入，清理跨项目 URL 和缓存范围；危险删除等待影响预览成功。",
        "• 批次名称后端强制，支持说明、用途、标签、不可变配置快照和请求指纹幂等。",
        "• batch CAS 比较 lock_version；attempt sequence 有唯一约束；outbox 有独占 claim、lease、claimant fencing 与指数退避。",
        "• 分析页展示数据截止、配置口径和报告版本；证据可跳转具体 run；冻结报告区分客户版与内部诊断版。",
        "• 统一 skeleton、后台刷新、stale cache 状态；移动导航具备 dialog、Tab、Escape 与焦点恢复。",
    ], h1, body)
    story.append(Paragraph("证据矩阵", h1))
    data = [["验收层", "状态", "证据"], ["后端单元与 HTTP 契约", "通过", "130 tests OK；条件项单独启用通过；migration v1-v6"], ["PostgreSQL schema", "通过", "v6，无 pending、无 drift"], ["双 worker kill/recovery", "通过", "6/6 success；duplicate current 0；expired lease 0"], ["Redis/DB 短断", "通过", "中断检测变红，恢复后转绿"], ["前端 unit/build", "通过", "6 files / 12 tests；入口 gzip 94.17 KB"], ["完整 HTTP suite", "通过", "含重复 retry 原子抢占专项"], ["全页面浏览器验收", "通过", "35/35；axe；Lighthouse 100"]]
    table = Table([[Paragraph(cell, body) for cell in row] for row in data], colWidths=[40 * mm, 24 * mm, 92 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e2d7")), ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#d1c8b9")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [table]
    story += section("真实故障演练结果", ["在隔离 PostgreSQL 16 与 Redis 7 中创建 6 个持久任务，启动 RQ worker，让一个 worker claim 后强制终止，再由 reconciler 回收租约并可靠重新派发。最终批次 completed，成功 6、失败 0、排队 0、运行中 0、重复 current 0、过期 lease 0。演练额外发现并修复了 JSON datetime 与空字符串时间戳两个 PostgreSQL 兼容问题。"], h1, body)
    story += section("剩余风险", ["• 磁盘阈值、SQLite lock/recovery 与 DB commit 后 RQ ack 前迟到 failure callback 已有确定性测试；真实压力演练仍可作为部署前加固项。", "• DeepSeek Web browser contract 已通过；真实账号下的登录失效、验证码、selector 变化和浏览器崩溃仍需要可控账号环境。"], h1, body)
    story += [Paragraph("当前裁决", h1), Paragraph("可靠性核心与 P0-P1 产品缺口已经实质修复；完整 HTTP、浏览器、可访问性和真实多 worker 故障演练均已通过，当前工作树签署为 P0-P1 验收完成。", body), PageBreak(), Paragraph("独立审计 Trace 摘要", title)]
    for name, filename in [("Trace A：产品与 PRD", "trace-a-product.md"), ("Trace B：可靠性与架构", "trace-b-reliability.md"), ("Trace C：UX 与 QA", "trace-c-ux-qa.md")]:
        story.append(Paragraph(name, h2))
        text = (REPORT_DIR / "traces" / filename).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            story.append(Paragraph(stripped.replace("`", ""), body))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def section(name, paragraphs, heading_style, body_style):
    items = [Paragraph(name, heading_style)]
    items.extend(Paragraph(value, body_style) for value in paragraphs)
    return items


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("HeitiCN", 8)
    canvas.setFillColor(colors.HexColor("#77736c"))
    canvas.drawString(18 * mm, 10 * mm, "manufacturing-geo-audit V2 HeavySkill")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


if __name__ == "__main__":
    main()
