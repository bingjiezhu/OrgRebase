from __future__ import annotations

import json

from tests.workspace.test_workspace_client import CONSOLE, run_node


def test_formed_baseline_never_claims_a_verified_change_loop_without_verified_receipts():
    app = (CONSOLE / "app.js").read_text()
    shell = (CONSOLE / "workspace-shell.js").read_text()
    summary = "function runSummary" + shell.split("function runSummary", 1)[1].split(
        "\n  function taskApprovalCount", 1)[0]
    boundary = "function renderActiveRunBoundary" + app.split("function renderActiveRunBoundary", 1)[1].split(
        "\nfunction renderCurrentTaskBadge", 1)[0]
    badge = "function renderCurrentTaskBadge" + app.split("function renderCurrentTaskBadge", 1)[1].split(
        "\nfunction terminalRunId", 1)[0]
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const labels={runPending:'pending',runRecorded:'recorded',runComplete:'verified',runActive:'active'};
const values={};let projection={status:'WAITING_FOR_VERIFIED_ARCHIVE'};
const context={copy:()=>labels,currentRunValueProjection:()=>projection,activeCompetition:()=>null,
 t:key=>key,text:(id,value)=>{values[id]=value;},byId:()=>({classList:{toggle(){}}}),displayToken:value=>value,exactAuditTitle(){}};
vm.createContext(context);vm.runInContext(__HELPERS__,context);
const state={schema_version:'orgrebase.workspace-state.v2',stage:'CURRENT',business_complete:true,
 quote:{version:'r1'},execution:{run_id:'baseline'},changes:{}};
assert.equal(context.runSummary(state),'recorded');context.renderCurrentTaskBadge(state);
assert.equal(values['semifinal-status-badge'],'header.currentTask.progress');
context.normalized=state;context.competition=null;context.currentRunComplete=false;
context.renderActiveRunBoundary(state);assert.equal(values['active-boundary-note'],'active.boundary.active');
projection={status:'PASS',receiptCount:0,approvalCount:0};context.currentRunComplete=true;
assert.equal(context.runSummary(state),'verified');context.renderCurrentTaskBadge(state);
assert.equal(values['semifinal-status-badge'],'header.currentTask.complete');
context.renderActiveRunBoundary(state);assert.equal(values['active-boundary-note'],'active.boundary.complete');
projection={status:'INVALID'};context.currentRunComplete=false;
assert.equal(context.runSummary(state),'recorded');
'''.replace("__HELPERS__", json.dumps(summary + "\n" + boundary + "\n" + badge)).replace("__BOUNDARY__", json.dumps(boundary)))
    for line in app.splitlines():
        if any(f'"{key}"' in line for key in (
            "active.boundary.complete", "header.currentTask.complete", "collaboration.local.complete.title",
            "collaboration.local.complete.detail",
        )) and ":" in line:
            assert "两次" not in line
            assert "both change-specific human approvals" not in line
            assert "币种确认稿" not in line
