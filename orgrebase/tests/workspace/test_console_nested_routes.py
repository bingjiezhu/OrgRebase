"""Bookmarked workspace routes preserve their panel and reject cross-page tabs."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")


def test_nested_routes_aliases_queries_and_invalid_combinations() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const context=vm.createContext({window:{location:{hash:''}},invalidRoute:false,activeTabs:{quote:'work',onboarding:'materials',assurance:'skills'}});
vm.runInContext(source.slice(source.indexOf('  const ROUTES ='),source.indexOf('  const STAGES ='))
 + source.slice(source.indexOf('  function routeFromHash('),source.indexOf('  function routeDescriptor(')),context);
for(const [hash,route,tab] of [
 ['#/assurance/skills','assurance','skills'],['#/assurance/operations','assurance','operations'],
 ['#/assurance/validation?run=abc','assurance','validation'],['#/quote/changes','quote','changes'],
 ['#/quote/collaboration','quote','collaboration'],['#/quote/agents','quote','collaboration'],
 ['#/onboarding/oac','onboarding','oac'],['#/onboarding/materials','onboarding','materials'],
 ['#agents','quote','collaboration'],['#/data?run=x','quote','changes'],['#/quote&tab=ignored','quote','work'],
 ['#/skills','assurance','skills'],
]) {
 context.window.location.hash=hash;assert.equal(context.routeFromHash(),route,hash);
 assert.equal(context.invalidRoute,false,hash);
 assert.equal(context.activeTabs[route],tab,hash);
}
for(const hash of ['#/quote/skills','#/assurance/changes','#/assurance/skills/extra','#/unknown/skills','#/quote/']) {
 const before=JSON.stringify(context.activeTabs);context.window.location.hash=hash;
 assert.equal(context.routeFromHash(),'onboarding',hash);
 assert.equal(context.invalidRoute,true,hash);
 assert.equal(JSON.stringify(context.activeTabs),before,'invalid routes cannot mutate a valid tab');
}
context.window.location.hash='#/quote';assert.equal(context.routeFromHash(),'quote');
assert.equal(context.invalidRoute,false,'valid navigation clears a previous invalid link');
''')
