You are refining a reusable Australian travel-insurance extraction schema.

Compare the current JSON schema baseline with the supplied PDS evidence. Return
only candidate field patches governed by the output contract; do not rewrite the
whole schema. Use the declared Travel product types in applies_to. Prefer stable
cross-insurer comparison fields and canonical snake_case names. Treat tables as
first-class evidence. Rare promotional wording, unsupported fields, and weak
inferences should be rejected or assigned low confidence. Identity changes,
renames, merges, moves, and type changes must be explicit so a human can review
them rather than being silently applied.
