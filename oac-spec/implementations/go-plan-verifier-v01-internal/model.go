package main

import "encoding/json"

type resourceMetadata struct {
	ID            string          `json:"id"`
	Namespace     string          `json:"namespace"`
	Revision      binary64Integer `json:"revision"`
	OwnerRef      *string         `json:"ownerRef,omitempty"`
	GovernanceRef *string         `json:"governanceRef,omitempty"`
	CreatedAt     string          `json:"createdAt"`
	EffectiveFrom *string         `json:"effectiveFrom,omitempty"`
	EffectiveTo   *string         `json:"effectiveTo,omitempty"`
	SourceRefs    []string        `json:"sourceRefs,omitempty"`
}

type organizationSnapshot struct {
	APIVersion string              `json:"apiVersion"`
	Kind       string              `json:"kind"`
	Metadata   resourceMetadata    `json:"metadata"`
	Spec       organizationSpec    `json:"spec"`
	Digest     string              `json:"digest"`
	rawRoot    jsonValue           `json:"-"`
	rawBytes   []byte              `json:"-"`
	nodeByID   map[string]*orgNode `json:"-"`
	roleByID   map[string]*roleDef `json:"-"`
	edgeByID   map[string]*depEdge `json:"-"`
	ruleByID   map[string]*rule    `json:"-"`
	dutyByID   map[string]*duty    `json:"-"`
	nodeIndex  map[string]int      `json:"-"`
}

type organizationSpec struct {
	Nodes                   []orgNode         `json:"nodes"`
	RoleDefinitions         []roleDef         `json:"roleDefinitions"`
	Principals              []principal       `json:"principals"`
	DependencyEdges         []depEdgeWire     `json:"dependencyEdges,omitempty"`
	ImpactRules             []json.RawMessage `json:"impactRules,omitempty"`
	UnknownTransitionDuties []duty            `json:"unknownTransitionDuties,omitempty"`
	SeparationConstraints   []separation      `json:"separationConstraints,omitempty"`
	Completeness            completeness      `json:"completeness"`
	parsedEdges             []depEdge         `json:"-"`
	parsedRules             []rule            `json:"-"`
}

type orgNode struct {
	NodeID          string  `json:"nodeId"`
	NodeType        string  `json:"nodeType"`
	DomainRef       string  `json:"domainRef"`
	OwnerRoleRef    *string `json:"ownerRoleRef,omitempty"`
	AdmissionStatus string  `json:"admissionStatus,omitempty"`
}

type roleDef struct {
	RoleID                 string   `json:"roleId"`
	DomainRef              string   `json:"domainRef"`
	Mission                string   `json:"mission"`
	ResponsibilityTypes    []string `json:"responsibilityTypes"`
	RequiredQualifications []string `json:"requiredQualifications,omitempty"`
	EffectCeiling          string   `json:"effectCeiling,omitempty"`
	AdmissionStatus        string   `json:"admissionStatus,omitempty"`
}

type principal struct {
	PrincipalID       string   `json:"principalId"`
	PrincipalType     string   `json:"principalType"`
	EligibleRoleRefs  []string `json:"eligibleRoleRefs"`
	QualificationRefs []string `json:"qualificationRefs,omitempty"`
	Status            string   `json:"status"`
	AdmissionStatus   string   `json:"admissionStatus,omitempty"`
}

type depEdgeWire struct {
	EdgeID            string          `json:"edgeId"`
	SourceRef         string          `json:"sourceRef"`
	TargetRef         string          `json:"targetRef"`
	RelationType      string          `json:"relationType"`
	TransferPredicate json.RawMessage `json:"transferPredicate"`
	AdmissionStatus   string          `json:"admissionStatus"`
}

type depEdge struct {
	EdgeID          string
	SourceRef       string
	TargetRef       string
	RelationType    string
	Predicate       predicate
	AdmissionStatus string
	Index           int
}

type predicateWire struct {
	PredicateVersion string           `json:"predicateVersion"`
	SemanticType     string           `json:"semanticType"`
	AfterState       string           `json:"afterState"`
	AfterValues      []string         `json:"afterValues,omitempty"`
	SubjectSelector  *subjectSelector `json:"subjectSelector,omitempty"`
	ScopeSelector    *scopeSelector   `json:"scopeSelector,omitempty"`
	RelationTypes    []string         `json:"relationTypes,omitempty"`
}

type legacyPredicateWire struct {
	SemanticType string   `json:"semanticType"`
	AfterValues  []string `json:"afterValues"`
}

type predicate struct {
	PredicateVersion string
	SemanticType     string
	AfterState       string
	AfterValues      []string
	SubjectSelector  *subjectSelector
	ScopeSelector    *scopeSelector
	RelationTypes    []string
	Legacy           bool
}

type subjectSelector struct {
	SubjectRefs []string `json:"subjectRefs,omitempty"`
	NodeTypes   []string `json:"nodeTypes,omitempty"`
	DomainRefs  []string `json:"domainRefs,omitempty"`
}

type scopeSelector struct {
	Refs            []string `json:"refs"`
	MatchMode       string   `json:"matchMode"`
	MissingBehavior string   `json:"missingBehavior"`
}

type contextualRuleWire struct {
	RuleID                      string          `json:"ruleId"`
	Applicability               json.RawMessage `json:"applicability"`
	TargetRef                   string          `json:"targetRef"`
	RequiredRoleRef             string          `json:"requiredRoleRef"`
	ObligationType              string          `json:"obligationType"`
	RequiredEvidence            []string        `json:"requiredEvidence"`
	PrerequisiteObligationTypes []string        `json:"prerequisiteObligationTypes,omitempty"`
	AdmissionStatus             string          `json:"admissionStatus"`
}

type legacyRuleWire struct {
	RuleID           string   `json:"ruleId"`
	SemanticType     string   `json:"semanticType"`
	AfterValues      []string `json:"afterValues"`
	TargetRef        string   `json:"targetRef"`
	RequiredRoleRef  string   `json:"requiredRoleRef"`
	ObligationType   string   `json:"obligationType"`
	RequiredEvidence []string `json:"requiredEvidence"`
	AdmissionStatus  string   `json:"admissionStatus"`
}

type rule struct {
	RuleID                      string
	Predicate                   predicate
	TargetRef                   string
	RequiredRoleRef             string
	ObligationType              string
	RequiredEvidence            []string
	PrerequisiteObligationTypes []string
	AdmissionStatus             string
	Legacy                      bool
	Index                       int
}

type duty struct {
	DutyID                      string          `json:"dutyId"`
	Applicability               json.RawMessage `json:"applicability"`
	TargetRef                   string          `json:"targetRef"`
	RequiredRoleRef             string          `json:"requiredRoleRef"`
	ObligationType              string          `json:"obligationType"`
	RequiredEvidence            []string        `json:"requiredEvidence"`
	PrerequisiteObligationTypes []string        `json:"prerequisiteObligationTypes,omitempty"`
	AdmissionStatus             string          `json:"admissionStatus"`
	Predicate                   predicate       `json:"-"`
	Index                       int             `json:"-"`
}

type separation struct {
	ConstraintID string `json:"constraintId"`
	LeftRoleRef  string `json:"leftRoleRef"`
	RightRoleRef string `json:"rightRoleRef"`
}

type completeness struct {
	Status                  string   `json:"status"`
	CoveredNodeRefs         []string `json:"coveredNodeRefs"`
	CoveredRelationTypes    []string `json:"coveredRelationTypes"`
	MaxDepth                int      `json:"maxDepth"`
	KnownGaps               []string `json:"knownGaps,omitempty"`
	DiscoveryRoleRef        string   `json:"discoveryRoleRef"`
	DiscoveryTargetRef      *string  `json:"discoveryTargetRef,omitempty"`
	DiscoveryObligationType *string  `json:"discoveryObligationType,omitempty"`
	DiscoveryEvidence       []string `json:"discoveryEvidence,omitempty"`
}

type semanticChangeSet struct {
	APIVersion string           `json:"apiVersion"`
	Kind       string           `json:"kind"`
	Metadata   resourceMetadata `json:"metadata"`
	Spec       changeSpec       `json:"spec"`
	Digest     string           `json:"digest"`
	rawRoot    jsonValue        `json:"-"`
	rawBytes   []byte           `json:"-"`
}

type changeSpec struct {
	DemandRef       string        `json:"demandRef"`
	SubjectRef      string        `json:"subjectRef"`
	SemanticType    string        `json:"semanticType"`
	Deltas          []changeDelta `json:"deltas"`
	ObservedAt      string        `json:"observedAt"`
	EffectiveAt     string        `json:"effectiveAt"`
	SourceRef       string        `json:"sourceRef"`
	ScopeRefs       []string      `json:"scopeRefs"`
	Reason          string        `json:"reason"`
	AdmissionStatus string        `json:"admissionStatus"`
}

type changeDelta struct {
	Path          string        `json:"path"`
	Operation     string        `json:"operation"`
	Before        observedValue `json:"before"`
	After         observedValue `json:"after"`
	BeforeVersion *string       `json:"beforeVersion,omitempty"`
	AfterVersion  *string       `json:"afterVersion,omitempty"`
}

type observedValue struct {
	State string          `json:"state"`
	Value json.RawMessage `json:"value,omitempty"`
}

type profileDerivationReport struct {
	APIVersion               string                       `json:"apiVersion"`
	Kind                     string                       `json:"kind"`
	ProfileID                string                       `json:"profileId"`
	ProfileVersion           string                       `json:"profileVersion"`
	SnapshotDigest           string                       `json:"snapshotDigest"`
	ChangeDigest             string                       `json:"changeDigest"`
	ApplicabilityEvaluations []applicabilityEvaluation    `json:"applicabilityEvaluations"`
	ImpactPaths              []impactPath                 `json:"impactPaths"`
	Obligations              []coverageObligation         `json:"obligations"`
	RequiredOrders           []derivationOrderRequirement `json:"requiredOrders"`
	UnresolvedRefs           []string                     `json:"unresolvedRefs"`
	RootApplicabilityUnknown bool                         `json:"rootApplicabilityUnknown"`
}

type applicabilityEvaluation struct {
	EvaluationID     string   `json:"evaluationId"`
	SourceRef        string   `json:"sourceRef"`
	PredicateVersion string   `json:"predicateVersion"`
	Result           string   `json:"result"`
	ReasonCodes      []string `json:"reasonCodes"`
	WitnessRefs      []string `json:"witnessRefs"`
}

type impactPath struct {
	PathID         string   `json:"pathId"`
	TargetRef      string   `json:"targetRef"`
	State          string   `json:"state"`
	EdgeRefs       []string `json:"edgeRefs"`
	RuleRefs       []string `json:"ruleRefs"`
	EvaluationRefs []string `json:"evaluationRefs,omitempty"`
	DutyRefs       []string `json:"dutyRefs,omitempty"`
	Origin         string   `json:"origin"`
	ReasonCodes    []string `json:"reasonCodes"`
	Truncated      bool     `json:"truncated"`
}

type coverageObligation struct {
	ObligationID     string   `json:"obligationId"`
	Origin           string   `json:"origin"`
	TargetRef        string   `json:"targetRef"`
	DomainRef        string   `json:"domainRef"`
	RequiredRoleRef  string   `json:"requiredRoleRef"`
	ObligationType   string   `json:"obligationType"`
	RequiredEvidence []string `json:"requiredEvidence"`
	PathRefs         []string `json:"pathRefs"`
	ResolutionState  string   `json:"resolutionState"`
}

type derivationOrderRequirement struct {
	PredecessorRoleRef       string     `json:"predecessorRoleRef"`
	SuccessorRoleRef         string     `json:"successorRoleRef"`
	ReasonRefs               []string   `json:"reasonRefs"`
	RoleWide                 bool       `json:"roleWide"`
	DependencyReasonRefs     []string   `json:"dependencyReasonRefs"`
	PrerequisiteReasonGroups [][]string `json:"prerequisiteReasonGroups"`
}

type obligationSeed struct {
	Origin                      string
	TargetRef                   string
	RequiredRoleRef             string
	ObligationType              string
	ResolutionState             string
	DomainRef                   string
	RequiredEvidence            []string
	PathRefs                    []string
	PrerequisiteObligationTypes []string
	PrerequisitePathRefs        []string
}

type organizationPlan struct {
	APIVersion string           `json:"apiVersion"`
	Kind       string           `json:"kind"`
	Metadata   resourceMetadata `json:"metadata"`
	Spec       planSpec         `json:"spec"`
	Digest     string           `json:"digest"`
}

type resourceRef struct {
	APIVersion string          `json:"apiVersion,omitempty"`
	Kind       string          `json:"kind"`
	Namespace  string          `json:"namespace"`
	ResourceID string          `json:"resourceId"`
	Revision   binary64Integer `json:"revision"`
	Digest     string          `json:"digest"`
}

type binary64Integer int64

type planSpec struct {
	SnapshotRef              resourceRef               `json:"snapshotRef"`
	ChangeRef                resourceRef               `json:"changeRef"`
	Status                   string                    `json:"status"`
	EffectCeiling            string                    `json:"effectCeiling"`
	CompilerID               string                    `json:"compilerId"`
	CompilerVersion          string                    `json:"compilerVersion"`
	ApplicabilityEvaluations []applicabilityEvaluation `json:"applicabilityEvaluations,omitempty"`
	ImpactPaths              []impactPath              `json:"impactPaths"`
	Obligations              []coverageObligation      `json:"obligations"`
	RoleInstances            []roleInstance            `json:"roleInstances"`
	WorkUnits                []workUnit                `json:"workUnits"`
	HappensBefore            []orderConstraint         `json:"happensBefore"`
	Decisions                []planDecision            `json:"decisions"`
	UnresolvedRefs           []string                  `json:"unresolvedRefs"`
	Minimality               minimalityClaim           `json:"minimality"`
}

type roleInstance struct {
	RoleInstanceID    string   `json:"roleInstanceId"`
	RoleDefinitionRef string   `json:"roleDefinitionRef"`
	PrincipalRef      string   `json:"principalRef"`
	Mission           string   `json:"mission"`
	ObligationRefs    []string `json:"obligationRefs"`
	QualificationRefs []string `json:"qualificationRefs"`
	EffectCeiling     string   `json:"effectCeiling"`
}

type workUnit struct {
	WorkUnitID                 string   `json:"workUnitId"`
	RoleInstanceRefs           []string `json:"roleInstanceRefs"`
	AccountableRoleInstanceRef string   `json:"accountableRoleInstanceRef"`
	ObligationRefs             []string `json:"obligationRefs"`
	EvidenceOutputs            []string `json:"evidenceOutputs"`
	EffectCeiling              string   `json:"effectCeiling"`
}

type orderConstraint struct {
	PredecessorRef string   `json:"predecessorRef"`
	SuccessorRef   string   `json:"successorRef"`
	ReasonRefs     []string `json:"reasonRefs"`
	Relation       string   `json:"relation,omitempty"`
}

type planDecision struct {
	DecisionID  string   `json:"decisionId"`
	SubjectRef  string   `json:"subjectRef"`
	InputClass  string   `json:"inputClass"`
	Disposition string   `json:"disposition"`
	ReasonCodes []string `json:"reasonCodes"`
}

type minimalityClaim struct {
	Level                   string   `json:"level"`
	ConsideredRoleRefs      []string `json:"consideredRoleRefs"`
	ConsideredPrincipalRefs []string `json:"consideredPrincipalRefs"`
	NonRemovableRefs        []string `json:"nonRemovableRefs"`
}
