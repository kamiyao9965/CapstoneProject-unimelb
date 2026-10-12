# Documentation index

These documents describe the current Health, Pet, Travel and Car workflow.
Choose the reference for your task. Project documentation is in English;
source PDFs and parsed Markdown retain their original content and language.

## What to read

| Task | Document |
| --- | --- |
| Understand the project and start it locally | [Root README](../README.md) |
| Run CLI/UI workflows, find outputs and recover failures | [Operator guide](user-guide.md) |
| Use Python interfaces or inspect CLI and JSON contracts | [API and file contracts](api.md) |
| Understand runtime flows, module ownership and design constraints | [Architecture](architecture.md) |
| Choose input, output and cache directories | [Project layout](project-layout.md) |
| Check dependency constraints before adding a package | [Dependency policy](dependency-policy.md) |
| Edit model prompts and shared templates | [Prompt guide](../prompts/README.md) |

The operator guide explains how to use the system. The API reference defines
supported interfaces and validation boundaries. Use current code and contracts
to verify behavior; successful validation or offline tests do not establish
extraction accuracy.

## Keeping documentation current

- Keep the root README focused on setup and entry points.
- Put procedures, command examples and recovery steps in the operator guide.
- Document interfaces and file formats in the API reference.
- Record module owners, design constraints and their rationale in architecture.
- Keep directory rules in project layout and dependency approvals in dependency policy.
- Update the affected references when behavior changes; verify local links.

Travel PostgreSQL initialization creates missing tables in a new database; it
does not upgrade existing tables. If an existing database needs upgrading,
prepare and review migration SQL against its actual schema before execution.
See the [storage workflow](user-guide.md#c-approve-the-storage-contract).

Do not commit source PDFs, credentials or experimental outputs as documentation.
