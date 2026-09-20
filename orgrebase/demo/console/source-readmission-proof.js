(() => {
  "use strict";
  const verified = new WeakMap();
  const same = (left,right) => window.OrgRebaseWire.canonical(left) === window.OrgRebaseWire.canonical(right);
  const digest = value => typeof value === "string" && /^sha256:[a-f0-9]{64}$/.test(value);
  const unique = values => new Set(values).size === values.length;
  function bound(receipt, runId, metadata) {
    try {
      const proof=receipt.group_evidence, group=proof.group, outcome=proof.outcome;
      if(verified.get(receipt)!==window.OrgRebaseWire.canonical(receipt))return false;
      const events=proof.source_events,owners=proof.owners,set=outcome.approval_set,base=outcome.rebase_receipt,successor=outcome.workspace_rebase_receipt,quote=outcome.quote;
      if(receipt.kind!=="source_readmission_group" || proof.schema_version!=="orgrebase.source-readmission-group-detail.v1" || group.schema_version!=="orgrebase.source-readmission-group.v1"
        || group.execution_run_id!==runId || group.run_envelope.run_id!==runId || group.id!==receipt.group_id || group.digest!==receipt.group_digest
        || group.change_set.state!=="READMISSION_GROUP_ADMITTED" || group.preview.state!=="READY" || group.preview.digest!==receipt.preview_digest
        || !Array.isArray(events) || events.length<2 || events.length>3 || !unique(events.map(item=>item.event_id)) || !unique(events.map(item=>item.slot_id)) || !unique(events.map(item=>item.proposal.id))
        || events.length!==group.events.length || events.length!==group.source_specs.length || events.length!==group.change_set.deltas.length
        || !same(events.map(item=>item.event_id),receipt.event_ids) || !Array.isArray(metadata))return false;
      for(let index=0;index<events.length;index++) {
        const event=events[index],ref=group.events[index],spec=group.source_specs[index],delta=group.change_set.deltas.find(item=>item.object_id===event.proposal.id),record=metadata.find(item=>item.event_id===event.event_id);
        if(!digest(event.digest)||!delta||!record||event.operation!=="READMIT"||event.organization_id!==group.tenant_id
          ||ref.event_id!==event.event_id||ref.event_digest!==event.digest||spec.owner_id!==event.owner_id||spec.object_id!==event.proposal.id
          ||spec.base_version!==event.base_version||spec.proposed_version!==event.proposal.version||spec.operation!=="READMIT"
          ||delta.admitted_by!==event.owner_id||delta.base_version!==event.base_version||delta.proposed_version!==event.proposal.version||!same(delta.proposed_value,event.proposal.payload.canonical_value)
          ||record.source_group_id!==group.id||record.event_digest!==event.digest||record.owner_id!==event.owner_id||record.slot_id!==event.slot_id||record.status!=="GROUP_APPLIED")return false;
      }
      const ownerIds=[...new Set(events.map(item=>item.owner_id))].sort();
      if(set.schema_version!=="orgrebase.rebase-approval-set.v1"||!same(ownerIds,owners.map(item=>item.owner_id).sort())||!same(ownerIds,set.members.map(item=>item.owner_id).sort())
        || !unique(owners.map(item=>item.owner_id))||!unique(set.members.map(item=>item.owner_id))||owners.length!==receipt.human_approval_count||owners.length!==receipt.owner_approvals.length)return false;
      for(const member of set.members) {
        const owner=owners.find(item=>item.owner_id===member.owner_id),approval=member.approval,review=owner.review_evidence,scopeEvents=events.filter(item=>item.owner_id===member.owner_id),sources=scopeEvents.map(item=>item.proposal.id).sort();
        const exposed=receipt.owner_approvals.find(item=>item.owner_id===member.owner_id);
        if(!same(member.source_ids,sources)||!same(owner.source_ids,sources)||!same(owner.source_approval,member)
          ||approval.change_set_digest!==group.change_set.digest||approval.preview_digest!==group.preview.digest||approval.minimal_rebase_certificate_digest!==group.minimal_rebase_certificate.digest
          ||!same(approval.authority_scope,[group.change_set.id,...sources].sort())||!digest(approval.digest)||!digest(member.digest)
          ||Date.parse(approval.approved_at)>=Date.parse(approval.expires_at)||!Number.isFinite(Date.parse(approval.approved_at))||!Number.isFinite(Date.parse(approval.expires_at))
          ||review.group_digest!==group.digest||review.preview_digest!==group.preview.digest||review.review_wait_satisfied!==true||review.review_not_before_epoch_ms!==group.review_not_before_epoch_ms
          ||!Number.isSafeInteger(review.approved_at_epoch_ms)||review.approved_at_epoch_ms<group.review_not_before_epoch_ms
          ||!same(Object.keys(owner.authorities).sort(),scopeEvents.map(item=>item.event_id).sort())
          ||!exposed||exposed.approval_digest!==approval.digest||exposed.source_approval_digest!==member.digest||exposed.actor_id!==approval.actor_id||!same(exposed.authorities,owner.authorities)||!same(exposed.review_evidence,review))return false;
        for(const event of scopeEvents) {
          const authority=owner.authorities[event.event_id];
          if(authority===null){if(approval.actor_id!==member.owner_id)return false;continue;}
          const provenance=authority.provenance,grant=authority.grant;
          if(authority.schema_version!=="orgrebase.workspace-approval-authority.v1"||authority.evidence_class!=="VERIFIED_EVENT_SCOPED_APPROVAL_AUTHORITY"
            ||provenance.approval_digest!==approval.digest||provenance.event_digest!==event.digest||provenance.identity.actor_id!==approval.actor_id)return false;
          if(approval.actor_id===member.owner_id){if(grant!==null||provenance.grant_digest!==null)return false;}
          else if(!grant||grant.event_id!==event.event_id||grant.event_digest!==event.digest||grant.execution_run_id!==runId||grant.workspace_id!==group.workspace_id||grant.tenant_id!==group.tenant_id
            ||grant.owner.actor_id!==member.owner_id||!same(grant.delegate,provenance.identity)||provenance.grant_digest!==authority.grant_digest||!same(grant.actions,["APPROVE","REJECT"])
            ||!(Date.parse(grant.issued_at)<=Date.parse(approval.approved_at)&&Date.parse(approval.approved_at)<Date.parse(grant.expires_at)))return false;
        }
      }
      const changeRef=`${group.change_set.id}@${group.change_set.revision}`,quoteRef=`${quote.id}@${quote.version}`;
      const transitions=base.transitions.filter(item=>item.object_id===quote.id), claims=base.applied_claims.map(item=>[item.object_id,item.from,item.to]).sort();
      return outcome.schema_version==="orgrebase.source-readmission-group-outcome.v1"&&outcome.group_id===group.id&&outcome.group_digest===group.digest
        &&base.schema_version==="orgrebase.rebase-receipt.v2"&&base.status==="COMPLETED"&&base.workflow_run_id===runId&&base.run_nonce===group.run_envelope.nonce
        &&base.approval_actor_id===null&&same(base.approval_set,set)&&base.approval_digest===set.digest&&base.approval_ref===set.id&&base.digest===receipt.rebase_receipt_digest&&set.digest===receipt.approval_digest
        &&base.change_set_ref===changeRef&&base.preview_ref===group.preview.id&&base.minimal_rebase_certificate_digest===group.minimal_rebase_certificate.digest&&same(base.revision_lock,group.preview.revision_lock)
        &&group.minimal_rebase_certificate.change_set_digest===group.change_set.digest&&group.minimal_rebase_certificate.preview_digest===group.preview.digest
        &&successor.status==="COMPLETED"&&successor.digest===receipt.workspace_receipt_digest&&successor.base_rebase_receipt_digest===base.digest&&successor.base_rebase_receipt_ref===base.id&&successor.successor_object_refs.includes(quoteRef)
        &&same(claims,events.map(event=>[event.proposal.id,event.base_version,event.proposal.version]).sort())&&transitions.length===1&&transitions[0].from===group.predecessor_ref&&transitions[0].to===quoteRef&&transitions[0].to_state==="CURRENT"
        &&quote.version===receipt.successor_quote_version&&quote.digest===receipt.successor_quote_digest&&receipt.predecessor_quote_ref===group.predecessor_ref;
    }catch(_){return false;}
  }
  async function verify(receipt) {
    const wire=receipt.group_evidence_wire,proof=receipt.group_evidence;
    if(wire?.scheme!=="orgrebase.jcs-safe-number.v1" || await window.OrgRebaseWire.digest(proof,wire.scheme)!==wire.digest)throw new Error("SOURCE_GROUP_ARCHIVE_WIRE_INVALID");
    for(const owner of proof.owners || [])for(const authority of Object.values(owner.authorities || {})) {
      if(authority===null)continue;
      // Authority documents contain strings/null/arrays only; their retained SHA scheme is unambiguous.
      if(await window.OrgRebaseWire.digest(authority.provenance)!==authority.provenance_digest || (authority.grant===null ? authority.grant_digest!==null : await window.OrgRebaseWire.digest(authority.grant)!==authority.grant_digest))throw new Error("SOURCE_GROUP_AUTHORITY_DIGEST_INVALID");
    }
    verified.set(receipt,window.OrgRebaseWire.canonical(receipt));
  }
  window.OrgRebaseSourceReadmissionProof=Object.freeze({verify,bound});
})();
