import click
from pathlib import Path
from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, StateSnapshot, now,
    _nucleate, _decay_evidence, _recalc_stability,
)
from .storage import load, save
from .entropy import calculate as calculate_entropy


WORKSPACE = Path.cwd()


def _load():
    return load(WORKSPACE)


def _save(state: WorkspaceState):
    state.updated_at = now()
    save(state, WORKSPACE)


# --- CLI ---

@click.group()
def main():
    """Liquid Loop — Workspace Cognitive Runtime"""
    pass


@main.command()
def init():
    """初始化 Liquid Loop 工作区"""
    s = _load()
    if s.anchors or s.evidences:
        click.echo("工作区已初始化，无需重复操作。")
        return
    s.version = "0.1.0"
    _save(s)
    click.echo(f"✓ Liquid Loop 工作区已初始化: {WORKSPACE}/.liquid/")


@main.command()
def status():
    """显示当前认知状态"""
    s = _load()
    ent = calculate_entropy(s)
    level = "GREEN" if ent < 0.3 else ("YELLOW" if ent < 0.6 else "RED")
    latest_ev = max((e.timestamp for e in s.evidences), default="无活动")
    click.echo(f"锚点: {len(s.anchors)} | 证据: {len(s.evidences)} | 记忆: {len(s.memories)} | 冲突: {len(s.conflicts)}")
    click.echo(f"熵值: {ent:.4f} [{level}] | 最新活动: {latest_ev}")


@main.command()
@click.argument("name")
@click.argument("description")
def anchor_add(name, description):
    """添加锚点"""
    s = _load()
    # 同名检查
    if any(a.name == name for a in s.anchors):
        click.echo(f"锚点 '{name}' 已存在。")
        return
    anchor = Anchor(name=name, description=description)
    s.anchors.append(anchor)
    _save(s)
    click.echo(f"✓ 锚点已添加: {name} ({anchor.id})")


@main.command()
def anchor_list():
    """列出所有锚点"""
    s = _load()
    if not s.anchors:
        click.echo("暂无锚点。使用 liquid anchor add <名称> <描述> 添加。")
        return
    for a in s.anchors:
        bar = "█" * int(a.stability * 10) + "░" * (10 - int(a.stability * 10))
        click.echo(f"[{a.stability:.2f}] {bar}  {a.name}: {a.description}")


@main.command()
@click.argument("anchor_name")
@click.argument("content")
def evidence_add(anchor_name, content):
    """为锚点添加证据"""
    s = _load()
    anchor = next((a for a in s.anchors if a.name == anchor_name), None)
    if not anchor:
        click.echo(f"锚点 '{anchor_name}' 不存在。请先创建。")
        return
    evidence = Evidence(anchor_id=anchor.id, content=content)
    s.evidences.append(evidence)
    anchor.evidence_ids.append(evidence.id)
    # 衰减 + 重算 + 成核
    _decay_evidence(s, anchor.id)
    _recalc_stability(s, anchor.id)
    _nucleate(s, anchor.id)
    _save(s)
    click.echo(f"✓ 证据已添加: [{anchor_name}] {content[:40]}... ({evidence.id})")


@main.command()
@click.option("--anchor", "-a", default=None, help="按锚点名称过滤")
def evidence_list(anchor):
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
    if not evs:
        click.echo("暂无证据。")
        return
    for e in sorted(evs, key=lambda x: x.timestamp, reverse=True):
        anchor_name = next((a.name for a in s.anchors if a.id == e.anchor_id), "?")
        click.echo(f"[{e.weight:.2f}] {e.timestamp[:19]} [{anchor_name}] {e.content}")


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
def check():
    """检查工作区熵值"""
    s = _load()
    ent = calculate_entropy(s)
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
    ent = calculate_entropy(s)
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


if __name__ == "__main__":
    main()
