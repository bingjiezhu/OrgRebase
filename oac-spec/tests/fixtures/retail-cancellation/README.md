# Proposed local organization mapping for tau2 retail task 113

`snapshot.json` and `change.json` preserve the local mapping created for a controlled disposable
outcome experiment. Their role assignments, impact rules, evidence obligations, request transition
and admission states are OAC-authored proposals. They are not upstream Gold labels, human approval,
or a customer deployment record.

Upstream task reference:
`https://github.com/sierra-research/tau2-bench/blob/672227c6b6676edc20d57ea53b7000262aae77b9/data/tau2/domains/retail/tasks.json`
with task ID `113`. The task references are synthetic; no user database, task oracle, credentials or
runtime authority is included in these fixtures. Upstream is MIT-licensed; see `THIRD_PARTY.yml`.

The mapping contains a request node owned by `role:review`, two order nodes owned by `role:cancel`,
three admitted legacy impact rules, complete local coverage, and no dependency or ordering rules.
These facts are fixed before the outcome experiment. The tests exercise profile/root integrity and
planning semantics; they do not execute a cancellation or prove real business benefit.
