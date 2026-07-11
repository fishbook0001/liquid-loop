import click
from pathlib import Path
from collections import Counter
from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, StateSnapshot,
    AnchorRelation, now, DensityLevel, CognitiveStage, LiquidityLevel,
)
from .storage import load, save, get_audit_chain
from .entropy import calculate
from .entropy import calculate


WORKSPACE = Path.cwd()


def _load():
    return load(WORKSPACE)


def _save(state: WorkspaceState):
    state.updated_at = now()
    save(state, WORKSPACE)


# Memory Nucleation: 同一 Anchor 下 >= 2 条一致 Evidence 成核
def _nucleate(state: WorkspaceState, anchor_id: str):
    group = [e for e in state.evidences if e.anchor_id == anchor_id]
    if len(group) < 2:
        return
    content_counts = Counter(e.content for e in group)
    for content, count in content_counts.items():
        if count >= 2:
            existing = [m for m in state.memories if m.content == content]
            if existing:
                continue
            evidence_ids = [e.id for e in group if e.content == content]
            confidence = min(count / len(group), 1.0)
            memory = Memory(
                content=content,
                evidence_ids=evidence_ids,
                confidence=confidence,
            )
            state.memories.append(memory)


def _decay_evidence(state: WorkspaceState, anchor_id: str):
    for e in state.evidences:
        if e.anchor_id == anchor_id:
            e.weight = max(e.weight * 0.95, 0.1)


def _recalc_stability(state: WorkspaceState, anchor_id: str):
    group = [e for e in state.evidences if e.anchor_id == anchor_id]
    if not group:
        return
    avg_weight = sum(e.weight for e in group) / len(group)
    for a in state.anchors:
        if a.id == anchor_id:
            a.stability = avg_weight
            # 刷新价值衰减 + 锚定强度 + 自动分类
            evidence_count = len(group)
            a.decay_value(evidence_count=evidence_count)
            a.recalc_strength(evidence_count)
            a.auto_classify(evidence_count)


# --- CLI ---

@click.group()
def main():
    """Liquid Loop — Workspace Cognitive Runtime v0.2"""
    pass


@main.command()
def init():
    """初始化 Liquid Loop 工作区"""
    s = _load()
    if s.anchors or s.evidences:
        click.echo("工作区已初始化，无需重复操作。")
        return
    s.version = "0.2.0"
    _save(s)
    click.echo(f"✓ Liquid Loop v0.2 工作区已初始化: {WORKSPACE}/.liquid/")


@main.command()
def status():
    """显示当前认知状态（含四维分类概览）"""
    s = _load()
    ent = calculate(s)
    level = "GREEN" if ent < 0.3 else ("YELLOW" if ent < 0.6 else "RED")
    latest_ev = max((e.timestamp for e in s.evidences), default="无活动")
    click.echo(f"锚点: {len(s.anchors)} | 证据: {len(s.evidences)} | 记忆: {len(s.memories)} | 冲突: {len(s.conflicts)} | 关系: {len(s.relations)}")
    click.echo(f"熵值: {ent:.4f} [{level}] | 最新活动: {latest_ev}")
    click.echo(f"版本: {s.version}")


@main.command()
def anchor_list():
    """列出所有锚点（含四维分类 + 价值衰减）"""
    s = _load()
    if not s.anchors:
        click.echo("暂无锚点。使用 liquid anchor add <名称> <描述> 添加。")
        return
    for a in s.anchors:
        a.decay_value()
        bar = "█" * int(a.stability * 10) + "░" * (10 - int(a.stability * 10))
        strength_bar = "■" * int(a.anchor_strength * 10) + "□" * (10 - int(a.anchor_strength * 10))
        ev_count = len([e for e in s.evidences if e.anchor_id == a.id])
        click.echo(
            f"[{a.stability:.2f}] {bar}  {a.name}: {a.description[:50]}"
        )
        click.echo(
            f"     分类: [{a.value_density}/{a.cognitive_stage}/{a.liquidity}] "
            f"证据:{ev_count} 价值:{a.value_score:.2f} "
            f"锚定:[{strength_bar}] {a.anchor_strength:.2f}"
        )


@main.command()
@click.argument("anchor_name")
@click.argument("content")
@click.option("--quality", "-q", default="normal", help="证据质量: strong/normal/weak")
def evidence_add(anchor_name, content, quality):
    """为锚点添加证据"""
    s = _load()
    anchor = next((a for a in s.anchors if a.name == anchor_name), None)
    if not anchor:
        click.echo(f"锚点 '{anchor_name}' 不存在。请先创建。")
        return
    evidence = Evidence(
        anchor_id=anchor.id,
        content=content,
        quality=quality if quality in ("strong", "normal", "weak") else "normal",
    )
    s.evidences.append(evidence)
    anchor.evidence_ids.append(evidence.id)
    _decay_evidence(s, anchor.id)
    _recalc_stability(s, anchor.id)
    _nucleate(s, anchor.id)
    _save(s)
    click.echo(f"✓ 证据已添加: [{anchor_name}] {content[:40]}... quality={evidence.quality}")


@main.command()
@click.option("--anchor", "-a", default=None, help="按锚点名称过滤")
@click.option("--quality", "-q", default=None, help="按质量过滤: strong/normal/weak")
def evidence_list(anchor, quality):
    """列出证据"""
    s = _load()
    evs = s.evidences
    if anchor:
        a = next((x for x in s.anchors if x.name == anchor), None)
        if not a:
            click.echo(f"锚点 '{anchor}' 不存在。")
            return
        evs = [e for e in evs if e.anchor_id == a.id]
        click.echo(f"--- {anchor} ---")
    if quality:
        evs = [e for e in evs if e.quality == quality]
        click.echo(f"--- quality={quality} ---")
    if not evs:
        click.echo("暂无证据。")
        return
    for e in sorted(evs, key=lambda x: x.timestamp, reverse=True):
        anchor_name = next((a.name for a in s.anchors if a.id == e.anchor_id), "?")
        click.echo(f"[{e.weight:.2f}|{e.quality}] {e.timestamp[:19]} [{anchor_name}] {e.content}")


@main.command()
def memory_list():
    """列出已形成的记忆"""
    s = _load()
    if not s.memories:
        click.echo("暂无记忆。当同一锚点下有 2 条以上一致证据时自动成核。")
        return
    for m in sorted(s.memories, key=lambda x: x.confidence, reverse=True):
        click.echo(f"[conf={m.confidence:.2f}] {m.formed_at[:19]} {m.content}")


@main.command()
@click.argument("source")
@click.argument("target")
@click.option("--type", "-t", "rel_type", default="relates_to",
              help="关系类型: depends_on/relates_to/supersedes/conflicts_with")
@click.option("--weight", "-w", default=1.0, help="关系强度 0.0~1.0")
def relate(source, target, rel_type, weight):
    """在两个锚点之间建立关系"""
    s = _load()
    src = next((a for a in s.anchors if a.name == source), None)
    tgt = next((a for a in s.anchors if a.name == target), None)
    if not src or not tgt:
        click.echo("源或目标锚点不存在。")
        return
    # 去重
    for r in s.relations:
        if r.source_id == src.id and r.target_id == tgt.id:
            click.echo(f"关系已存在: {source} → {target} [{r.relation_type}]")
            return
    rel = AnchorRelation(
        source_id=src.id,
        target_id=tgt.id,
        relation_type=rel_type,
        weight=min(max(weight, 0.0), 1.0),
    )
    s.relations.append(rel)
    _save(s)
    click.echo(f"✓ 关系已建立: {source} --[{rel_type}]--> {target}")


@main.command()
def relation_list():
    """列出所有锚点关系"""
    s = _load()
    if not s.relations:
        click.echo("暂无关系。使用 liquid relate 建立。")
        return
    name_map = {a.id: a.name for a in s.anchors}
    for r in s.relations:
        src = name_map.get(r.source_id, "?")
        tgt = name_map.get(r.target_id, "?")
        click.echo(f"{src} --[{r.relation_type}:{r.weight:.1f}]--> {tgt}")


@main.command()
def check():
    """检查工作区熵值（六维）"""
    s = _load()
    ent = calculate(s)
    if ent < 0.3:
        level, icon = "GREEN", "✓"
    elif ent < 0.6:
        level, icon = "YELLOW", "⚠"
    else:
        level, icon = "RED", "✗"
    click.echo(f"{icon} 熵值: {ent:.4f} [{level}]")
    if ent >= 0.6:
        click.echo("建议: 检查锚点漂移、添加新证据或解决冲突。")


@main.command()
def snapshot():
    """保存当前状态快照"""
    s = _load()
    ent = calculate(s)
    snap = StateSnapshot(
        entropy=ent,
        anchor_count=len(s.anchors),
        evidence_count=len(s.evidences),
        memory_count=len(s.memories),
        conflict_count=len(s.conflicts),
    )
    s.snapshots.append(snap)
    _save(s)
    click.echo(f"✓ 快照已保存 (熵值: {ent:.4f})")


@main.command()
def snapshots():
    """列出历史快照"""
    s = _load()
    if not s.snapshots:
        click.echo("暂无快照。使用 liquid snapshot 保存。")
        return
    for i, snap in enumerate(s.snapshots):
        click.echo(f"[{i}] {snap.timestamp[:19]}  熵={snap.entropy:.4f}  "
                    f"A:{snap.anchor_count} E:{snap.evidence_count} "
                    f"M:{snap.memory_count} C:{snap.conflict_count}")


@main.command()
def audit():
    """查看审计链状态"""
    s = _load()
    ac = get_audit_chain(WORKSPACE)
    click.echo(f"审计根哈希: {ac.root}")
    click.echo(f"链长度: {len(ac._chain)}")
    click.echo(f"当前state快照校验: anchors={len(s.anchors)} evidence={len(s.evidences)}")


@main.command()
def audit_log():
    """查看审计日志"""
    ac = get_audit_chain(WORKSPACE)
    path = WORKSPACE / ".liquid" / "audit.log"
    if not path.exists():
        click.echo("审计日志为空。")
        return
    with open(path, "r") as f:
        content = f.read().strip()
    click.echo(content[-2000:] if len(content) > 2000 else content)


@main.command()
@click.argument("name")
@click.argument("description")
@click.option("--density", "-d", default="medium", help="价值密度: high/medium/low")
@click.option("--stage", "-s", default="raw", help="认知阶段: raw/wip/crystallized/tooling")
def anchor_add(name, description, density, stage):
    """添加锚点（手动指定分类，支持 auto_classify 自动刷新）"""
    s = _load()
    if any(a.name == name for a in s.anchors):
        click.echo(f"锚点 '{name}' 已存在。")
        return
    anchor = Anchor(
        name=name,
        description=description,
        value_density=density if density in ("high", "medium", "low") else "medium",
        cognitive_stage=stage if stage in ("raw", "wip", "crystallized", "tooling") else "raw",
    )
    # 自动分类：基于名称+描述
    anchor.auto_classify(0)
    s.anchors.append(anchor)
    ac = get_audit_chain(WORKSPACE)
    ac.append("anchor_add", f"{anchor.id}:{name}")
    _save(s)
    click.echo(f"✓ 锚点已添加: {name} ({anchor.id}) [{anchor.value_density}/{anchor.cognitive_stage}/{anchor.liquidity}]")


if __name__ == "__main__":
    main()