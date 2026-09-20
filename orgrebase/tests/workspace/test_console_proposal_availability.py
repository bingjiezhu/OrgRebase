"""A changed proposal operation never discards edits or authorizes a stale draft."""

from __future__ import annotations

import json
import shutil

import pytest

from tests.workspace.test_console_wrapup_changes import HARNESS
from tests.workspace.test_workspace_client import run_node


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("operations", [[], ["READMIT"]], ids=["source_expired", "readmit_required"])
def test_edited_draft_survives_operation_withdrawal_and_recovery_without_post(operations):
    run_node(HARNESS + r'''
(async()=>{
 await tick();await tick();
 await edit('Unsent product update','source:original');
 nodes.get('change-proposal-reason').value='Keep the original review context';
 await nodes.get('change-proposal-reason').emit('input');
 const selected=configuration.fields[0];
 const originalBase=structuredClone(selected.current);
 selected.allowed_operations=OPERATIONS;
 selected.blocked_reason=selected.allowed_operations.length ? null : 'SOURCE_VALIDITY_EXPIRED';
 selected.current.state=selected.allowed_operations.length ? 'STALE' : 'CURRENT';
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(selected.current.version,originalBase.version);
 assert.equal(selected.current.digest,originalBase.digest);
 assert.equal(nodes.get('change-submit').disabled,true,
  'operation withdrawal must block an edited draft even when its base is unchanged');
 assert.equal(nodes.get('change-submit').dataset.unavailable,'true');
 assert.equal(nodes.get('change-value').value,'Unsent product update');
 assert.equal(nodes.get('change-source').value,'source:original');
 assert.equal(nodes.get('change-proposal-reason').value,'Keep the original review context');
 assert.equal(nodes.get('change-operation').value,'UPDATE','refresh must not silently select readmission');
 await nodes.get('change-proposal-form').emit('submit');
 assert.equal(submissions().length,0,'a stale operation cannot reach the proposal API');
 nodes.get('change-submit').dataset.unavailable='false';
 nodes.get('change-submit').disabled=false;
 await nodes.get('change-proposal-form').emit('submit');
 assert.equal(submissions().length,0,'submission rechecks eligibility even if the DOM gate is cleared');

 selected.allowed_operations=['UPDATE'];selected.blocked_reason=null;
 selected.current=structuredClone(originalBase);
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(nodes.get('change-submit').disabled,false);
 assert.equal(nodes.get('change-value').value,'Unsent product update','eligibility recovery preserves edited values');
 assert.equal(nodes.get('change-source').value,'source:original');
 assert.equal(nodes.get('change-proposal-reason').value,'Keep the original review context');
 assert.equal(submissions().length,0,'refreshing eligibility never resubmits a draft');
 await nodes.get('change-proposal-form').emit('submit');
 assert.equal(submissions().length,1);
 assert.equal(submissions()[0].request.body.operation,'UPDATE',
  'temporary READMIT eligibility must not silently change the intended operation');
 assert.equal(submissions()[0].request.body.value,'Unsent product update');
 assert.equal(submissions()[0].request.body.source_ref,'source:original');
 assert.equal(submissions()[0].request.body.reason,'Keep the original review context');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("OPERATIONS", json.dumps(operations)))
