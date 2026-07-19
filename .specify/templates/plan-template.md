# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]
**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]  
**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]  
**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]  
**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]  
**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]
**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]  
**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]  
**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]  
**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

<!--
  ACTION REQUIRED: Every constitution section below MUST get an explicit
  ✅ / ⚠️ / N/A verdict plus a one-line justification. A section may never be
  silently omitted — "not applicable" is a valid verdict, but it must be
  stated, not implied by absence. Add a row for any section not listed here
  if the constitution has been amended since this template was last synced.
-->

| §   | Section                                   | Verdict (✅/⚠️/N/A) | Justification |
| --- | ------------------------------------------ | ------------------- | -------------- |
| 1   | Project Identity                            |                      |                |
| 2   | Stack Constraints                           |                      |                |
| 3   | Data Model Authority                        |                      |                |
| 4   | API Contract Rules                          |                      |                |
| 5   | Test-Driven Development                     |                      |                |
| 6   | Task Atomicity                              |                      |                |
| 7   | Spec Gate                                   |                      |                |
| 8   | Refactor Cadence                            |                      |                |
| 9   | Architecture Document                       |                      |                |
| 10  | Feedback Intake                             |                      |                |
| 11  | Git Branching Model                         |                      |                |
| 12  | CI Requirements                             |                      |                |
| 13  | v1 Scope Boundaries                         |                      |                |
| 14  | Status Pipeline                             |                      |                |
| 15  | Architecture Must Not Foreclose Future Caps |                      |                |
| 16  | Agent Role Boundaries                       |                      |                |
| 17  | Decision Records                            |                      |                |
| 18  | Bug Resolution Protocol                     |                      |                |
| 19  | Test Gate                                   |                      |                |

### Stack Constraint Check

<!--
  ACTION REQUIRED: List every dependency, framework, tool, or external
  service named anywhere in this plan (Technical Context, research.md,
  contracts/, data-model.md). Match each one against the constitution §2
  stack table by exact name. Any dependency not already in that table is a
  new stack constraint and requires a constitution amendment BEFORE this
  plan can pass — propose the amendment in this section, do not silently
  add the dependency.
-->

| Dependency/tool named in this plan | In constitution §2? | Amendment needed? |
| ----------------------------------- | -------------------- | ------------------- |
|                                      |                       |                      |

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
