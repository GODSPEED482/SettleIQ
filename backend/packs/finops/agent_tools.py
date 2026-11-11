"""Finance agent tools (spec 8.2). Called by core through the pack registry, never imported.

The Agent computes no money: it asks this tool, which runs the same settlement calculation as
the domain engine, over the investigated case's evidence snapshot.
"""

from typing import Any

from packs.finops.engine import F, _Index, batch_observations, batch_settlement
from packs.finops.settlement import treatment


def settlement_variance(snapshot: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Expected Net, bank credit and variance for each settlement batch in the case, computed
    with decimal Money under the OPEN-10 treatment. Pass entity_id to name one batch. A batch
    with an unresolved input or mixed currencies is reported as not computable, never guessed."""
    index = _Index(snapshot)
    batches = index.of_class(f"{F}SettlementBatch")
    if args.get("entity_id"):
        batches = [b for b in batches if b == args["entity_id"]]
    results, evidence = [], set()
    for batch in batches:
        settlement = batch_settlement(index, batch)
        if settlement is None:
            results.append({"batch_id": batch, "computable": False})
            continue
        result = settlement.result
        observations = batch_observations(index, settlement)
        evidence.update([batch, settlement.bank, *observations])
        results.append({
            "batch_id": batch,
            "computable": True,
            "bank_entity_id": settlement.bank,
            "currency": settlement.bank_credit.currency,
            "expected_net": format(result.expected_net.amount, "f"),
            "bank_credit": format(settlement.bank_credit.amount, "f"),
            "variance": format(result.variance.amount, "f"),
            "included_entries": result.included,
            "excluded_entries": result.excluded,
            "unclassified_entries": result.unclassified,
            "observation_ids": observations,
        })
    return {"result": {"batches": results, "treatment_version": treatment()["version"]},
            "evidence_ids": sorted(evidence)}
