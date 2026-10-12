# Four-vertical integration review

Date: 2026-10-12. Scope: CSV branch ancestry plus the confirmed unified workflow
in ADR-007 and `docs/specs/vertical-unification.md`.

## Review result

No unresolved blocker was found in the reviewed integration. Offline verification
ran 627 tests: 626 passed, one real PostgreSQL test skipped with its environment
variable unset. Compileall, both main/refinement CLI help commands and staged/
unstaged diff checks passed. Provider fakes and synthetic files establish workflow
behavior; no real provider request, PDF upload/download, database migration/load,
raw-data edit or actual schema approval was performed.

| Axis | Evidence and limits |
| --- | --- |
| Correctness | Strict descriptor/native compilation, closed nested projection, bounded repairs, uniform product arrays, explicit amendment grouping, qualified citations, grouped resume and holdout exclusion have regressions. Domain rules record applicability; grouped Car skips superseded-clause completeness checks. Judge/approval proofs bind all current inputs. Grouped SQL tests exercise actual uniqueness rules and rollback after a later document-link failure. |
| Readability | Shared owners retain request, sampling, parsing, artifact and transaction orchestration. Domain modules hold Pet/Car semantics; imported legacy candidate CLI entry points were removed. Current README/API/operator guide and ADR describe the implemented boundaries. |
| Architecture | All four manifests select prompts, inventory and allowlisted rules. One provider layer, repair runner and sampler remain. Canonical/CSV capabilities are independent of database capability. Travel uses the immutable historical mapping template and a separate new approved-output path. |
| Security | Source paths/hashes and explicit links validate before use; no remote schema references or recursive expansion. Delivery proves exact input identity, CSV validates before atomic write/refuses overwrite, and approval folders cannot overlap extraction folders. No new dependency, credential or raw-PDF commit. |
| Performance | Provider repair budget is unchanged. Source indexes use parsed blocks, group sampling checks each member's content, and grouped storage remains one transaction per extraction. Offline tests do not establish live cost or parser/model latency. |

## Graph and source checks

GitNexus staged analysis reports 106 changed runtime/config/test files, 563
changed symbols and 242 affected flows, rated critical because the integration
touches shared orchestration. Reviewed areas include cached extraction, delivery
approval, schema generation/refinement, parser rendering, CSV and SQL preparation.
The local index was rebuilt, but MCP commit-staleness metadata still lagged and
flow construction reported truncation. Graph counts are navigation evidence;
current source, targeted regressions and the complete offline suite are the
correctness evidence. No absent graph edge was treated as proof of no caller.

## Remaining operator actions and existing issues

Car's new 37-field v6 Canonical candidate remains unapproved. New Canonical
versions require human review before judge/delivery. Existing Travel databases
need the reviewed `001-grouped-release-identity.sql` migration before grouped
loads; initialization does not migrate them. Live provider/PDF and PostgreSQL
checks require separate authorization.

Existing unrelated identity and holdout retry issues remain in `docs/fix.md`.
The strict-runtime issue is resolved offline, with its proposed live trial still
pending. The labelled-list comparison issue is removed from unified main by the
user's explicit feature-removal decision; its historical algorithm is not claimed
to be repaired. Historical branches/artifacts and local report drafts are preserved.
