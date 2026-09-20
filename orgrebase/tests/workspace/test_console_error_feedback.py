"""Approval errors give a next step and expose only a bounded support reference."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")


def test_approval_time_feedback_supports_existing_error_shapes_and_valid_incident_only() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/app.js','utf8');
const context=vm.createContext({});
vm.runInContext('const DEFAULT_LANGUAGE="zh-CN";'+source.slice(source.indexOf('const I18N ='),source.indexOf('function modelSuggestion('))
 + source.slice(source.indexOf('function errorMessage('),source.indexOf('async function api(')),context);
for(const lang of ['zh-CN','en']) {
 vm.runInContext(`currentLanguage=${JSON.stringify(lang)}`,context);
 for(const code of ['APPROVAL_TIME_WINDOW_INVALID','APPROVAL_TIMESTAMP_INVALID']) {
  const direct=context.errorMessage({detail:{code}},{status:409});
  assert.equal(direct,context.errorMessage({detail:{code:'EVIDENCE_INTEGRITY_FAILED',message:code}},{status:409}));
  assert(!direct.includes('APPROVAL_'));assert.match(direct,lang==='en'?/preview again/:/重新预演/);
  const incident='0123456789abcdef'.repeat(2);
  assert(context.errorMessage({detail:{code,incident_id:incident}},{status:409}).endsWith(incident));
  for(const value of [null,12,'<script>secret</script>','a'.repeat(31),'a'.repeat(33),'A'.repeat(32)]) {
   assert.equal(context.errorMessage({detail:{code,incident_id:value}},{status:409}),direct);
  }
 }
 const text=context.errorMessage({detail:{code:'OTHER_PUBLIC_CODE',message:'PRIVATE_SERVER_DETAIL'}},{status:500});
 assert(!text.includes('PRIVATE_SERVER_DETAIL'));assert(text.includes('OTHER_PUBLIC_CODE'));
}
''')
