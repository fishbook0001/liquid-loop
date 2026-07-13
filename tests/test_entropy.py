import pytest
from liquid_loop.workspace import WorkspaceState, Anchor, Evidence
from liquid_loop.entropy import (
    calculate, calculate_detail,
    anchor_drift, conflict_density, evidence_fragmentation,
    activity_gap, value_decay_entropy, strength_entropy,
    retrospective_decay_entropy, behavioral_drift_entropy,
    generalization_erosion_entropy
)

def test_calculate_returns_float():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'evidence 1')
    ent = calculate(state)
    assert isinstance(ent, float)
    assert 0.0 <= ent <= 1.0

def test_calculate_detail_has_all_keys():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'evidence 1')
    detail = calculate_detail(state)
    assert 'combined' in detail
    assert 'anchor_drift' in detail
    assert 'cpe_retrospective_decay' in detail
    assert 'cpe_behavioral_drift' in detail
    assert 'cpe_generalization_erosion' in detail

def test_anchor_drift():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'evidence 1')
    drift = anchor_drift(state)
    assert 0.0 <= drift <= 1.0

def test_conflict_density():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'evidence 1')
    density = conflict_density(state)
    assert 0.0 <= density <= 1.0

def test_evidence_fragmentation():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'evidence 1')
    frag = evidence_fragmentation(state)
    assert 0.0 <= frag <= 1.0

def test_activity_gap():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'evidence 1')
    gap = activity_gap(state)
    assert 0.0 <= gap <= 1.0
