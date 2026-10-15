locals {
  ref_condition = join(" || ", [
    for ref in sort(tolist(var.allowed_refs)) :
    "assertion.ref == \"${ref}\""
  ])

  identity_clauses = concat(
    ["assertion.repository == \"${var.repository}\""],
    var.repository_id == null ? [] : ["assertion.repository_id == \"${var.repository_id}\""],
    var.repository_owner_id == null ? [] : ["assertion.repository_owner_id == \"${var.repository_owner_id}\""],
    ["(${local.ref_condition})"],
  )
  base_condition = join(" && ", local.identity_clauses)

  # Empty additional_attribute_conditions keeps a single AND of repository, ids, and ref.
  # Extras are OR-ed and weaken the provider. The root stack passes none (SD-74).
  attribute_condition = (
    length(var.additional_attribute_conditions) == 0
    ? local.base_condition
    : join(" || ", concat(
      ["(${local.base_condition})"],
      var.additional_attribute_conditions,
    ))
  )
}
