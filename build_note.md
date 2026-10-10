# Build Note — Employee Change Propagation with Claude Exception Analysis

## 1. Chosen slice and leverage rationale

I selected **change propagation** as the primary workflow. A single employee lifecycle change can require coordinated updates to HR records, identity/access, equipment logistics, and an onboarding/cohort tracker. If these updates are performed manually, the employee record can become inconsistent across systems and create access or onboarding delays. This slice provides a useful end-to-end demonstration of validation, per-system mapping, orchestration, partial failure handling, retry safety, idempotency, and auditability.

The other slices were deliberately deprioritized for this prototype:

- **Cross-system data validation** is valuable for detecting drift, but it would primarily report discrepancies rather than demonstrate the controlled propagation of a new change.
- **Exception monitoring** is valuable for surfacing operational issues, but it focuses on detection and explanation after a failure rather than the core lifecycle workflow.

This is a qualitative leverage argument, not a measured ROI claim. A production business case should measure manual touches avoided, propagation latency, failure rate, and time to remediate exceptions.

## 2. System map, source of truth, and ownership

The incoming event is represented by a mock Ashby event. For this prototype, the canonical employee record is the normalized input contract; it is not a claim that Ashby owns every employee field in a real deployment. Before production, the business must designate the authoritative source per field and define conflict/correction rules.

| Field / concern | Proposed source of truth (production design; confirm with system owner) | Destinations / flow | Example break mode |
|---|---|---|---|
| Candidate/recruiting event | Ashby, for recruiting-stage data | Ashby event → workflow | Missing or repeated event ID |
| Employee ID, legal name, work email, hire date | Workday, once the person is an employee | Workday → identity and operational systems as approved | Required field missing or Workday rejects an update |
| Account username and access lifecycle | Okta/Lumos, for identity/access state | Workflow → Okta/Lumos | Identity API unavailable or account policy rejects request |
| Shipping location and equipment need-by date | Employee location/start date from the approved HR source; equipment system owns fulfillment status | Workflow → equipment system | Unsupported shipping location or vendor timeout |
| Onboarding cohort assignment | Onboarding/cohort tracker owns cohort-specific status; employee attributes come from the approved HR source | Workflow → cohort tracker | Schema mismatch or rejected date |
| Collaboration and knowledge tools | Google Workspace, Slack, and Highspot are potential production destinations where the onboarding process requires them | Workflow → approved connector(s) | Permission denied, rate limit, or API outage |

**Prototype boundary:** only Workday, Okta/Lumos, Equipment, and Cohort Tracker are represented as local mock destinations. Ashby is represented by sample events. Google Workspace, Slack, and Highspot are stack-alignment considerations, not implemented connectors. No live vendor API is called by the mock connectors.

### Example deterministic transformation

Canonical input (fictional):

```json
{"employee_id":"E201","name":"Alex Morgan","email":"Alex.Morgan@example.com","location":"Bengaluru","start_date":"2026-11-10"}
```

- Workday payload: `worker_id=E201`, `legal_name=Alex Morgan`, `work_email=Alex.Morgan@example.com`, `work_location=Bengaluru`, `hire_date=2026-11-10`.
- Okta/Lumos payload: `employee_id=E201`, `username=alex.morgan@example.com` (lowercased), `display_name=Alex Morgan`, `location=Bengaluru`.
- Equipment payload: `employee_id=E201`, `recipient_name=Alex Morgan`, `shipping_location=Bengaluru`, `needed_by=2026-11-10`.
- Cohort Tracker payload: `employee_id=E201`, `name=Alex Morgan`, `location=Bengaluru`, `cohort_start_date=2026-11-10`.

These mappings are illustrative mock schemas and must be confirmed against real destination API contracts before integration.

## 3. Stack discipline and tool choice

| Assignment / existing-stack item | Prototype representation | Production consideration |
|---|---|---|
| Ashby | Sample incoming event payload | Use the approved Ashby event/API connector and authenticate it |
| Workday | `MockDestination` named Workday | Use an approved Workday API/connector and contract tests |
| Okta/Lumos | `MockDestination` named Okta/Lumos | Use approved identity lifecycle APIs and least-privilege credentials |
| Equipment | Local mock connector | Connect to the actual equipment fulfilment system selected by the organization |
| Cohort Tracker | Local mock connector | Connect to the real onboarding/cohort tool |
| Google Workspace, Slack, Highspot | Not implemented; considered in the destination map | Add only if the employee lifecycle workflow requires them and the team approves access |
| n8n / Zapier | Not used in this prototype | Could be suitable for simpler connector-first flows; evaluate existing integrations, governance, retry semantics, and operational ownership |
| Python | Deterministic orchestration, validation, transformation, retries, idempotency, audit, tests, and MCP server | Chosen here to make control flow explicit and testable; this prototype does not prove Python is universally better than n8n/Zapier |

The core engine uses Python's standard library. The optional MCP server requires the MCP SDK. The project uses mock connectors so it can be demonstrated without enterprise credentials or changing real employee data.

## 4. Data model and flow

Canonical employee fields are `employee_id`, `name`, `email`, `location`, and `start_date`; each event also contains a stable `event_id`. The engine validates required fields, a basic email shape, and ISO `YYYY-MM-DD` dates before any destination write. It then creates a deterministic key from the event ID and canonical payload and transforms the record independently for each destination.

## 5. Failure handling, retries, and idempotency

- The configured retry limit is **3 total attempts per destination** (`max_retries=3`). The current prototype retries immediately; it does **not** implement a delay or exponential backoff. Production should add exponential backoff with jitter and rate-limit-aware handling.
- Only simulated `ConnectionError` exceptions are retried. Other connector/API failure classes would need explicit handling in real adapters.
- If a destination succeeds, its success is remembered in process memory. Re-running a partially failed event in the same process skips destinations that already succeeded and retries unfinished destinations.
- A fully completed duplicate event with the same event ID and canonical payload is a no-op. The automated test `test_duplicate_event_does_not_write_twice` checks that destination write counts do not increase on the duplicate call.
- If a destination exhausts all three attempts, the workflow reports a partial failure and requests human review. Claude analysis is advisory only; it cannot approve provisioning or perform destination writes.
- Audit entries record validation rejection, retry/failure, destination success, exception-analysis provider, and overall status.

**Durability limitation:** event keys, completed destinations, mock records, and audit entries are in memory and reset on process restart. Production requires durable event/destination state and audit storage, plus a clear event replay and reconciliation policy.

## 6. Claude's role and boundaries

When a destination exhausts retries, `ClaudeAPIAnalyzer` can explain the technical failure and suggest a safe next step. It receives the destination name, attempt count, and technical error—not employee name, email, start date, or other employee fields. Its structured response is validated. Python remains the control plane for validation, retry policy, transformations, destination writes, idempotency, and audit logging. If no `ANTHROPIC_API_KEY` is configured, the clearly labelled `OfflineDemoAnalyzer` is used; that is a mock, not a live Claude call. The live Claude API path was not verified as part of the reported offline run.

## 7. AI tools, ownership, and a concrete correction

**Tools used:** ChatGPT and VS Code Agentic Chat were used as development assistants for design guidance, implementation/troubleshooting, and documentation. Suggestions were reviewed and tested rather than accepted automatically.

**AI-generated issue caught and manually corrected:** during MCP server setup, an AI-assisted SDK/import recommendation was incompatible with the installed MCP SDK package/version. Running the server produced `ModuleNotFoundError` for `mcp.server.fastmcp`. I investigated the compatibility issue, removed the incompatible package version, and installed the compatible MCP SDK v1 range using:

```powershell
python -m pip uninstall mcp -y
python -m pip install "mcp[cli]>=1.0.0,<2.0.0"
```

I then verified `mcp_server.py` syntax and confirmed that the VS Code MCP client discovered the three tools. This correction was based on the observed import failure and subsequent runtime verification. It illustrates why SDK-version compatibility must be checked against the installed package rather than relying solely on AI-generated guidance.

**Validation caveat:** the user-reported validation run through VS Code Agentic Chat did not reveal a validation mistake. The example above is the separate SDK compatibility issue, not a claim that the validation tests failed.

## 8. Demo and verification evidence

The `evidence/` directory contains captured output from running the demo and automated test suite. The automated tests cover successful propagation, duplicate-event no-op behavior, invalid email/date rejection, transient retry recovery, exhausted failure, offline exception analysis, and retry after partial failure. Re-run the commands below for generating the logs:

```powershell
python main.py
python -m unittest discover -s tests -v
python -m py_compile mcp_server.py
```

The reported MCP demonstration discovered three tools and successfully processed one fictional employee event across all four mock destinations. These are mock-system results, not proof of live vendor integration.

## 9. Productionization priorities

1. Replace mock adapters with authenticated vendor clients and contract tests.
2. Confirm authoritative field ownership, destination schemas, conflict rules, and employee lifecycle policy with system owners.
3. Persist event state, per-destination completion, and audit records in durable storage.
4. Add exponential backoff with jitter, timeouts, rate-limit handling, and a dead-letter/replay process.
5. Add secrets management, least privilege, PII minimization, retention controls, and access audit.
6. Add alerting, metrics, reconciliation jobs, and integration tests.
7. Bound Claude API cost/timeouts, defend against untrusted error-text prompt injection, and require human review for sensitive decisions.
