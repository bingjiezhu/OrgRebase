"""Context explanations use recorded counts, never private excluded sources."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_context_exclusions_are_bilingual_and_do_not_invent_missing_reasons() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/app.js','utf8');
const context=vm.createContext({DEFAULT_LANGUAGE:'zh-CN',currentLanguage:'zh-CN'});
vm.runInContext(source.slice(source.indexOf('const I18N ='),source.indexOf('let currentLanguage'))
  + source.slice(source.indexOf('function t('),source.indexOf('function modelSuggestion('))
  + source.slice(source.indexOf('function contextExclusionSummary('),source.indexOf('function agentWorkContext(')),context);
const projection={excluded_count:3,excluded_reason_counts:{OTHER_AUTHORITY_DOMAIN:2,FORBIDDEN_OUTPUT_FIELD:1}};
assert.equal(context.contextExclusionSummary(projection),'记录的排除原因：其他职责域 2 项 · 禁止输出 1 项');
context.currentLanguage='en';
assert.equal(context.contextExclusionSummary(projection),'Recorded exclusion reasons: other authority domain: 2 · output forbidden: 1');
for(const value of [null,{}, {excluded_count:2}, {...projection,excluded_count:4},
 {excluded_count:1,excluded_reason_counts:{'private:source':1}},
 {excluded_count:1,excluded_reason_counts:{OTHER:1.5}},
 {excluded_count:1,excluded_reason_counts:{OTHER:'1'}},
 {excluded_count:0,excluded_reason_counts:{OTHER:0}}]) {
 assert.equal(context.contextExclusionSummary(value),'','absent or inconsistent evidence has no invented explanation');
}
assert(context.contextExclusionSummary({excluded_count:1,excluded_reason_counts:{OTHER:1}}).includes('other reasons: 1'));
''')
