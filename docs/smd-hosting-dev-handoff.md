# SMD Hosting DEV handoff

**Processing** — objective `10b332ba-ec84-43b8-8323-02f61b0602e1`.

**Executor** — the existing authorized Codex runtime with private DEV Git,
image publication, deployment and Vultr access. The current objective landed
in a restricted Coder task worktree. Route the objective to the existing
execution context used for the successful Codex/Vultr run.

## DEV promotion in authorized Codex

Carry forward the changes in the assigned worktree based on
`429aeda7513f195c1456dea3e7adabe57dedc9a5`. They apply checkpoint
`0f98646` with a single-project limit in both the portal and API. Review
migration 035 against the authoritative release's existing migration sequence.
Use the private DEV release path and record the deployed commit SHA.

Confirm that the real DEV browser can list and preflight one existing main
project from SMD Hosting. Check that unauthenticated machine poll and result
requests are rejected, and that requests from the authorized Codex runtime reach
the configured dispatch handlers. Exercise the configured DEV ingress. The restricted
Coder preview proxy currently allowlists only its existing scoped reads and
login/logout. Its upstream points to the main SMD origin.

## Provisioning in authorized Codex

Recover the prior successful Codex/Vultr invocation and provider receipt
from its execution environment. Use the existing Codex runtime credential
reference for read-only instance, region, plan and pricing checks. Record the
prior receipt identifier, region, plan ID, CPU, memory, storage and exact
monthly cost, including any required add-ons. Keep credentials in the
authorized runtime's credential store.

Connect the authorized Codex runtime to the signed handoff. The checkpoint
labels the transport `POST /operator-machine/install-poll`. Treat that route
as candidate transport pending verification of the existing dispatcher.
Verify the release signature, central MIG grant, operation lifetime,
project and enrollment binding, and signed destination and plan digest.
Before dispatching the existing create action, require explicit approval for
the priced configuration. Persist the operation ID and provider resource
identity so that an interrupted run can reconcile the instance before retrying.

Install the digest-pinned stack from the internal release registry. Keep
local MIG and BRIPe off by default, require central MIG, and prohibit public
Git writes. Enroll the target operator, verify its project binding and
cross-project isolation, and check portal and vault over the deployed path.
Use the enrolled host signing key to post the result to
`POST /operator-machine/install-result`. Attach the enrollment and isolation
evidence to the receipt record. The checkpoint's receipt schema accepts the
instance ID, release digest and portal/vault readiness and does not itself
assert enrollment or isolation.

## Evidence and gates

Run the real DEV browser/API preflight with provider reads only. Leave the
create action undispatched until the exact offer receives approval. A signed
handoff exercise using fixture inputs can establish protocol behavior but
cannot establish that a new host works. For final acceptance, record the DEV
SHA, endpoint responses, provider instance ID, target enrollment proof,
portal and vault health observations, and the signed receipt ID and payload.

The target host operator enrollment is a post-install acceptance check.
Provisioning credentials belong to the authorized Codex runtime.

The worktree API sources compiled and an isolated plan fixture confirmed
distinct portal origins and storage names. Git staging failed because the
shared worktree index is read-only. HTTPS probes to the SMD and Vultr hosts
failed at DNS resolution, and the last recorded preview verification was
for `429aeda`. No new VM, DEV deployment, live operator receipt or validated
price came from this workspace.

## Canonical default image gate

The DEV source available to this task contains signed Compose service digests
and bootstrap artifact digests. It does not identify the canonical Vultr host
image or its build and release contract. Recover that record from the
authoritative image release owner before configuring the DEV provider profile.
The current worktree requires the signed release to carry
`default_host_image` with `version`, SHA-256 `digest`, and
`vultr_snapshot_id`. Map the authoritative record to this contract or adapt
the contract to the existing release format before deployment. The signed
MIG plan binds the same identity. Generic Ubuntu is forbidden as a fallback.
A supported bootstrap requires evidence from the canonical release contract
and an explicit integration.

Before provider create, the authorized Codex runtime needs to resolve the approved snapshot
and verify its mapping to the signed build digest using authoritative
release evidence. Before reporting installed, verify the running host's
image identity, baseline services and security checks, central MIG
enrollment, project isolation, and portal and vault health. Include the
observed identity in the signed receipt as `observed_host_image`.
Command Center rejects an installed Vultr receipt if that identity is
missing or differs from the approved plan.

## Reusable coding skills

Track this capability separately from the P0 host acceptance. The available
SMD API, portal and release sources do not expose a skill registry,
recurrence detector or extraction workflow. The API image build does not
show agent skill packaging. Verify the canonical host image's agent version,
installed skill discovery configuration and release manifest before deciding
whether to extend the existing mechanism.

Use completed, reviewed feature implementations as evidence of recurring code
or process patterns. For a repeatable verification, first run one feature
workflow and confirm it produces no candidate. Then run an independent
feature execution with the same reusable steps and different inputs. Record
both execution references and confirm the agent proposes one objective-scoped
candidate with a shared implementation, documented inputs and outputs,
a version, and tests. Repeat the evaluation to check that it
recognizes the existing candidate.

Review the candidate and run its tests before publication through the
existing skill mechanism, if present. Test reuse on another relevant
objective with different inputs. Record the agent and skill versions,
source examples, and review, test and reuse results. Apply ordinary code
review and testing throughout extraction, refactoring and reuse. External
actions invoked through a skill remain subject to their existing runtime
access controls.

## Native AI governance conformance

Use the canonical BRIPe AI governance use architecture as the reference for
SMD's native agent, tool and skill decisions. Keep central MIG as the
independent execution authorization. The existing public BRIPe address in
the signed host plan is for structural computation. Normal SMD agent
governance has no dependency on that service.

The local BRIPe fetch task contains a SmartDev checkout. Its Git origin is
the SmartDev repository. The canonical BRIPe source revision, governance
modules, policy format and receipt contract are absent from the accessible
release evidence. Recover them through the authorized BRIPe source checkout
and record the authoritative revision and exact module references.

Compare the reference implementation with the canonical SMD agent dispatcher.
The available SmartDev API provides objective contracts and evidence in
`api/governance.py`, project and lease-scoped MCP bindings in
`api/project_mcp.py`, and separate MIG verification for signed host grants.
Use those existing enforcement points where applicable. Confirm the native
agent entry point and policy lifecycle against the canonical agent release
before deciding which reference code to reuse.

For image conformance, verify applicability for routine deterministic work,
an applicable AI action with sufficient evidence, an applicable action with
missing authority or insufficient evidence, and a valid native decision
followed by a central MIG denial. Exercise tool and skill dispatch through
the normal agent path. Record the canonical image and agent versions, policy
revision, reference contract revision, decision evidence and replay result,
MIG result, and dispatch outcome. Verify native governance with the BRIPe
structural endpoint unavailable.
