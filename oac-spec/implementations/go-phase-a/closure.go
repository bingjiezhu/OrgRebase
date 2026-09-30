package main

import (
	"encoding/json"
	"errors"
	"sort"
)

type closureInput struct {
	Root            string        `json:"root"`
	MaxDepth        int64         `json:"maxDepth"`
	MaxPathPrefixes int64         `json:"maxPathPrefixes"`
	Nodes           []closureNode `json:"nodes"`
	Edges           []closureEdge `json:"edges"`
}

type closureNode struct {
	ID        string `json:"id"`
	Admission string `json:"admission"`
}

type closureEdge struct {
	ID        string `json:"id"`
	Source    string `json:"source"`
	Target    string `json:"target"`
	Result    string `json:"result"`
	Admission string `json:"admission"`
	Covered   bool   `json:"covered"`
}

type closurePath struct {
	EdgeRefs    []string `json:"edgeRefs"`
	ReasonCodes []string `json:"reasonCodes"`
	State       string   `json:"state"`
	TargetRef   string   `json:"targetRef"`
	Truncated   bool     `json:"truncated"`
}

type falseFrontier struct {
	Authoritative bool     `json:"authoritative"`
	EdgeID        string   `json:"edgeId"`
	EdgeRefs      []string `json:"edgeRefs"`
	SourceRef     string   `json:"sourceRef"`
	TargetRef     string   `json:"targetRef"`
}

type closureReport struct {
	Kind           string          `json:"kind"`
	RootRef        string          `json:"rootRef"`
	Paths          []closurePath   `json:"paths"`
	FalseFrontiers []falseFrontier `json:"falseFrontiers"`
	UnresolvedRefs []string        `json:"unresolvedRefs"`
}

type closurePathState struct {
	path    closurePath
	reasons map[string]struct{}
	visited map[string]struct{}
}

type closureTraversal struct {
	input       closureInput
	nodes       map[string]closureNode
	outgoing    map[string][]closureEdge
	paths       []*closurePathState
	frontiers   []falseFrontier
	prefixCount int64
}

func closureMicroOperation(payload json.RawMessage) (any, *operationError) {
	topKeys := []string{"root", "maxDepth", "maxPathPrefixes", "nodes", "edges"}
	if err := requireObjectKeys(payload, topKeys, topKeys); err != nil || validateClosureNestedShape(payload) != nil {
		return nil, schemaError()
	}
	var input closureInput
	if err := decodeClosed(payload, &input); err != nil || !validNonEmptyString(input.Root, 4096) || input.MaxDepth < 1 || input.MaxPathPrefixes < 1 || len(input.Nodes) == 0 || input.Edges == nil {
		return nil, schemaError()
	}
	if input.MaxDepth > resourceLimits["maxSemanticDepth"] || input.MaxPathPrefixes > resourceLimits["maxPathPrefixes"] ||
		len(input.Nodes) > int(resourceLimits["maxNodes"]) || len(input.Edges) > int(resourceLimits["maxEdges"]) {
		return nil, &operationError{status: statusResourceExhausted, code: codeResourceExceeded}
	}

	nodes := make(map[string]closureNode, len(input.Nodes))
	for _, node := range input.Nodes {
		if !validNonEmptyString(node.ID, 4096) || !oneOf(node.Admission, "admitted", "candidate", "disputed", "retracted") {
			return nil, schemaError()
		}
		if _, exists := nodes[node.ID]; exists {
			return nil, schemaError()
		}
		nodes[node.ID] = node
	}
	rootNode, exists := nodes[input.Root]
	if !exists || rootNode.Admission == "retracted" {
		return nil, &operationError{status: statusError, code: codeCTKInputInvalid}
	}

	edgeIDs := make(map[string]struct{}, len(input.Edges))
	outgoing := make(map[string][]closureEdge)
	for _, edge := range input.Edges {
		if !validNonEmptyString(edge.ID, 4096) || !validNonEmptyString(edge.Source, 4096) || !validNonEmptyString(edge.Target, 4096) ||
			!oneOf(edge.Result, "TRUE", "FALSE", "UNKNOWN") ||
			!oneOf(edge.Admission, "admitted", "candidate", "disputed", "retracted") {
			return nil, schemaError()
		}
		if _, exists := edgeIDs[edge.ID]; exists {
			return nil, schemaError()
		}
		edgeIDs[edge.ID] = struct{}{}
		if _, exists := nodes[edge.Source]; !exists {
			return nil, schemaError()
		}
		if _, exists := nodes[edge.Target]; !exists {
			return nil, schemaError()
		}
		outgoing[edge.Source] = append(outgoing[edge.Source], edge)
	}
	for source := range outgoing {
		sort.Slice(outgoing[source], func(i, j int) bool { return outgoing[source][i].ID < outgoing[source][j].ID })
	}

	rootReasons := make(map[string]struct{})
	rootState := "affected"
	if nodes[input.Root].Admission != "admitted" {
		rootState = "unknown"
		rootReasons["CANDIDATE_INPUT_NOT_AUTHORITY"] = struct{}{}
	}
	seed := &closurePathState{
		path: closurePath{
			EdgeRefs:    []string{},
			ReasonCodes: []string{},
			State:       rootState,
			TargetRef:   input.Root,
			Truncated:   false,
		},
		reasons: rootReasons,
		visited: map[string]struct{}{input.Root: {}},
	}
	traversal := closureTraversal{
		input:       input,
		nodes:       nodes,
		outgoing:    outgoing,
		paths:       []*closurePathState{seed},
		frontiers:   make([]falseFrontier, 0),
		prefixCount: 1,
	}
	if traversal.visit(seed) {
		return nil, &operationError{status: statusResourceExhausted, code: codeResourceExceeded}
	}
	return traversal.report(), nil
}

func validateClosureNestedShape(payload json.RawMessage) error {
	value, err := parseJSON(payload)
	if err != nil || value.kind != kindObject {
		return errors.New("invalid closure object")
	}
	nodesValue, ok := objectValue(value, "nodes")
	if !ok || nodesValue.kind != kindArray {
		return errors.New("invalid nodes")
	}
	for _, node := range nodesValue.array {
		if !hasExactObjectKeys(node, []string{"id", "admission"}) {
			return errors.New("invalid node shape")
		}
	}
	edgesValue, ok := objectValue(value, "edges")
	if !ok || edgesValue.kind != kindArray {
		return errors.New("invalid edges")
	}
	for _, edge := range edgesValue.array {
		if !hasExactObjectKeys(edge, []string{"id", "source", "target", "result", "admission", "covered"}) {
			return errors.New("invalid edge shape")
		}
	}
	return nil
}

func objectValue(object jsonValue, key string) (jsonValue, bool) {
	if object.kind != kindObject {
		return jsonValue{}, false
	}
	for _, member := range object.object {
		if member.key == key {
			return member.value, true
		}
	}
	return jsonValue{}, false
}

func hasExactObjectKeys(object jsonValue, expected []string) bool {
	if object.kind != kindObject || len(object.object) != len(expected) {
		return false
	}
	set := make(map[string]struct{}, len(expected))
	for _, key := range expected {
		set[key] = struct{}{}
	}
	for _, member := range object.object {
		if _, ok := set[member.key]; !ok {
			return false
		}
	}
	return true
}

func (t *closureTraversal) visit(current *closurePathState) bool {
	edges := t.eligibleEdges(current)
	if int64(len(current.path.EdgeRefs)) == t.input.MaxDepth {
		hasNonFalseContinuation := false
		for _, edge := range edges {
			if edge.Result != "FALSE" {
				hasNonFalseContinuation = true
			} else {
				// maxDepth limits propagation, not FALSE-frontier observation. Compute
				// authority from the prefix before any sibling continuation truncates it.
				t.appendFalseFrontier(current, edge)
			}
		}
		if hasNonFalseContinuation {
			current.path.State = "unknown"
			current.path.Truncated = true
			current.reasons["IMPACT_SEARCH_TRUNCATED"] = struct{}{}
		}
		return false
	}

	for _, edge := range edges {
		if edge.Result == "FALSE" {
			t.appendFalseFrontier(current, edge)
			continue
		}
		t.prefixCount++
		if t.prefixCount > t.input.MaxPathPrefixes {
			return true
		}
		child := t.continuePath(current, edge)
		t.paths = append(t.paths, child)
		if t.visit(child) {
			return true
		}
	}
	return false
}

func (t *closureTraversal) eligibleEdges(current *closurePathState) []closureEdge {
	eligible := make([]closureEdge, 0)
	for _, edge := range t.outgoing[current.path.TargetRef] {
		if edge.Admission == "retracted" || t.nodes[edge.Target].Admission == "retracted" {
			continue
		}
		if _, cycle := current.visited[edge.Target]; cycle {
			continue
		}
		eligible = append(eligible, edge)
	}
	return eligible
}

func (t *closureTraversal) appendFalseFrontier(current *closurePathState, edge closureEdge) {
	edgeRefs := appendString(current.path.EdgeRefs, edge.ID)
	authoritative := current.path.State == "affected" && edge.Admission == "admitted" && edge.Covered &&
		t.nodes[edge.Source].Admission == "admitted" && t.nodes[edge.Target].Admission == "admitted"
	t.frontiers = append(t.frontiers, falseFrontier{
		Authoritative: authoritative,
		EdgeID:        edge.ID,
		EdgeRefs:      edgeRefs,
		SourceRef:     edge.Source,
		TargetRef:     edge.Target,
	})
}

func (t *closureTraversal) continuePath(current *closurePathState, edge closureEdge) *closurePathState {
	reasons := cloneSet(current.reasons)
	if edge.Result == "UNKNOWN" {
		reasons["APPLICABILITY_INPUT_UNKNOWN"] = struct{}{}
	}
	if edge.Admission != "admitted" {
		reasons["CANDIDATE_EDGE_NOT_AUTHORITY"] = struct{}{}
	}
	if t.nodes[edge.Source].Admission != "admitted" || t.nodes[edge.Target].Admission != "admitted" {
		reasons["CANDIDATE_INPUT_NOT_AUTHORITY"] = struct{}{}
	}
	if !edge.Covered {
		reasons["GRAPH_COVERAGE_PARTIAL"] = struct{}{}
	}
	state := "unknown"
	if current.path.State == "affected" && edge.Result == "TRUE" && edge.Admission == "admitted" && edge.Covered &&
		t.nodes[edge.Source].Admission == "admitted" && t.nodes[edge.Target].Admission == "admitted" {
		state = "affected"
	}
	visited := cloneSet(current.visited)
	visited[edge.Target] = struct{}{}
	return &closurePathState{
		path: closurePath{
			EdgeRefs:    appendString(current.path.EdgeRefs, edge.ID),
			ReasonCodes: []string{},
			State:       state,
			TargetRef:   edge.Target,
			Truncated:   false,
		},
		reasons: reasons,
		visited: visited,
	}
}

func (t *closureTraversal) report() closureReport {
	paths := make([]closurePath, len(t.paths))
	unresolved := make(map[string]struct{})
	for index, state := range t.paths {
		state.path.ReasonCodes = sortedSet(state.reasons)
		paths[index] = state.path
		if state.path.State == "unknown" {
			unresolved[state.path.TargetRef] = struct{}{}
		}
	}
	sort.Slice(paths, func(i, j int) bool {
		if comparison := compareStringSlices(paths[i].EdgeRefs, paths[j].EdgeRefs); comparison != 0 {
			return comparison < 0
		}
		if paths[i].TargetRef != paths[j].TargetRef {
			return paths[i].TargetRef < paths[j].TargetRef
		}
		return paths[i].State < paths[j].State
	})
	frontiers := make([]falseFrontier, len(t.frontiers))
	copy(frontiers, t.frontiers)
	sort.Slice(frontiers, func(i, j int) bool {
		if comparison := compareStringSlices(frontiers[i].EdgeRefs, frontiers[j].EdgeRefs); comparison != 0 {
			return comparison < 0
		}
		return frontiers[i].EdgeID < frontiers[j].EdgeID
	})
	return closureReport{
		Kind:           "ClosureMicroReport",
		RootRef:        t.input.Root,
		Paths:          paths,
		FalseFrontiers: frontiers,
		UnresolvedRefs: sortedSet(unresolved),
	}
}

func cloneSet(source map[string]struct{}) map[string]struct{} {
	result := make(map[string]struct{}, len(source))
	for value := range source {
		result[value] = struct{}{}
	}
	return result
}

func sortedSet(values map[string]struct{}) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func appendString(values []string, value string) []string {
	result := make([]string, len(values)+1)
	copy(result, values)
	result[len(values)] = value
	return result
}

func compareStringSlices(left, right []string) int {
	limit := len(left)
	if len(right) < limit {
		limit = len(right)
	}
	for index := 0; index < limit; index++ {
		if left[index] < right[index] {
			return -1
		}
		if left[index] > right[index] {
			return 1
		}
	}
	switch {
	case len(left) < len(right):
		return -1
	case len(left) > len(right):
		return 1
	default:
		return 0
	}
}
