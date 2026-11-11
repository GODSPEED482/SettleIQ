"""The packaged safety suites and the G5 acceptance trace (tasks 5.12 and 5.17, spec 9.7.2).

`app/release/suites.yaml` names, per certified capability, the tests the attestation workflow
runs in the built image; the signer refuses an empty category or a listed test that did not
pass (AC-REL-02). These checks keep that list honest: every node names a module-level test that
exists, so the signer's JUnit matching can see it, and no listed test reads repository files the
image does not hold. TRACE maps each criterion of the two suites to its evidence for the
acceptance sheet (task 6.5).
"""

import importlib
import inspect

import pytest

from app.release import payloads

SUITES = payloads.parse_suites(payloads.SUITES_FILE.read_text(encoding="utf-8"))
NODES = sorted({node for suite in SUITES.values()
                for tests in suite.categories.values() for node in tests})

TRACE = {
    # Task 5.12
    "AC-MERCH-01": [
        "test_internal_merchant::test_correction_changes_exactly_one_row_and_readback_sees_completed",
        "test_worker::test_a_live_grant_corrects_one_payment_and_resolves_after_readback",
    ],
    "AC-MERCH-02": [
        "test_internal_merchant::test_already_completed_payment_is_stale_with_no_mutation",
        "test_internal_merchant::test_concurrent_change_after_precondition_read_is_stale",
        "test_worker::test_stale_state_fails_closed_and_returns_the_case_to_human_review",
    ],
    "AC-MERCH-03": [
        "test_internal_merchant::test_unknown_ambiguous_and_foreign_targets_fail_closed_alike",
        "test_internal_merchant::test_same_payment_id_under_another_merchant_is_never_touched",
        "test_internal_merchant::test_another_tenants_source_or_tenant_id_reveals_nothing",
    ],
    "AC-MERCH-04": [
        "test_internal_merchant::test_only_pending_to_completed_validates",
    ],
    "AC-MERCH-05": [
        "test_internal_merchant::test_same_key_and_payload_returns_the_original_outcome_once",
        "test_internal_merchant::test_same_key_with_a_different_payload_is_refused_without_mutation",
        "test_internal_merchant::test_concurrent_attempts_under_one_key_mutate_once",
        "test_internal_merchant::test_keys_are_scoped_to_the_merchant",
        "test_worker::test_an_unreachable_merchant_is_retried_under_the_same_key",
    ],
    "AC-MERCH-06": [
        "test_internal_merchant::test_undeclared_fields_are_rejected",
        "test_internal_merchant::test_sql_text_in_an_identifier_is_rejected",
        "test_internal_merchant::test_every_statement_is_a_constant_with_bind_parameters_only",
        "test_internal_merchant::test_parameter_models_match_the_manifest",
        "test_internal_merchant::test_the_adapter_role_can_only_read_and_correct_status",
        "test_internal_merchant::test_the_adapter_role_can_run_the_declared_statements",
    ],
    "AC-MERCH-07": [
        "test_contracts::test_internal_merchant_publishes_only_its_two_keys",
        "test_release_attestation::test_neither_capabilitys_attestation_authorizes_the_other_or_another_tenant",
    ],
    "AC-AUTHZ-06": [
        "test_catalogue::test_pol_03_principal_ceilings",
        "test_catalogue::test_investigation_agent_cannot_act",
        "test_worker::test_the_grant_keys_are_worker_only",
        "test_rls::test_a_worker_without_a_live_grant_resolves_to_nothing",
    ],
    "AC-AUTHZ-08": [
        "test_execution_chain::test_a_grant_is_consumed_once",
        "test_execution_chain::test_an_expired_grant_cannot_be_consumed",
        "test_execution_chain::test_a_grant_is_bound_to_exactly_its_execution",
        "test_execution_chain::test_a_grant_without_its_approval_is_refused",
        "test_playbook_gateway::test_live_reevaluates_the_approved_request_and_binds_one_grant",
        "test_worker::test_a_grant_runs_once",
        "test_worker::test_a_grant_is_bound_to_its_execution_and_tenant",
        "test_worker::test_an_expired_grant_runs_nothing",
    ],
    "AC-AUTHZ-15": [
        "test_playbook_gateway::test_an_approval_in_shadow_is_blocked_and_issues_nothing",
        "test_worker::test_shadow_after_the_grant_blocks_the_worker",
        "test_worker_refunds::test_shadow_after_the_grant_sends_nothing_to_paypal",
    ],
    "AC-AUTHZ-16": [
        "test_playbook_gateway::test_live_without_a_passing_attestation_is_blocked_by_pol14",
        "test_playbook_gateway::test_a_grant_is_refused_when_the_gate_fails_after_the_decision",
        "test_worker::test_a_failing_attestation_at_the_worker_blocks",
        "test_release_attestation::test_live_needs_and_records_a_valid_attestation",
    ],
    "AC-AUTHZ-17": [
        "test_route_guard::test_worker_route_is_not_on_the_public_app",
        "test_route_guard::test_human_is_rejected_by_the_worker_route",
        "test_route_guard::test_the_worker_app_reads_no_session_cookie",
        "test_worker::test_a_human_session_is_refused_before_any_side_effect",
        "test_worker::test_the_worker_publishes_no_port_and_shares_no_network_with_the_browser",
    ],
    "AC-PLY-08": [
        "test_playbook_gateway::test_without_required_approval_the_request_goes_straight_to_execution",
        "test_pb002::test_pb001_follows_the_tenants_latest_policy",
        "test_pb002::test_pb002_definition_always_requires_approval",
        "test_pb002::test_pb002_needs_approval_even_when_the_tenant_says_otherwise",
    ],
    "AC-PLY-09": [
        "test_execution_chain::test_a_full_chain_with_approval_is_accepted",
        "test_execution_chain::test_an_execution_decision_follows_its_own_requests_decision",
        "test_playbook_gateway::test_a_request_and_its_decision_exist_before_any_approval",
    ],
    "AC-PLY-10": [
        "test_execution_chain::test_an_attempt_records_its_binding",
        "test_execution_chain::test_append_only_chain_tables_refuse_change",
        "test_worker::test_a_live_grant_corrects_one_payment_and_resolves_after_readback",
    ],
    "AC-PLY-11": [
        "test_execution_chain::test_required_approval_cannot_be_omitted",
        "test_execution_chain::test_no_approval_may_be_attached_when_none_is_required",
        "test_execution_chain::test_tenant_a_cannot_reference_tenant_bs_chain",
    ],
    # Task 5.17
    "AC-PP-08": [
        "test_worker_refunds::test_an_approved_duplicate_is_refunded_and_resolved_only_after_readback",
        "test_worker_refunds::test_a_pending_refund_is_never_resolution",
        "test_worker_refunds::test_an_ambiguous_refund_is_settled_under_the_same_request_id",
    ],
    "AC-PLY-02": [
        "test_pb002::test_equal_amounts_on_two_orders_are_not_a_duplicate",
        "test_paypal_g4::test_ac_ply_02_duplicate_capture_proof",
    ],
    "AC-EXEC-MODE-03": [
        "test_execution_mode::test_switching_one_scope_live_leaves_the_other_unchanged",
        "test_execution_mode::test_returning_one_scope_to_shadow_leaves_the_other_live",
        "test_execution_mode::test_each_playbook_runs_under_its_own_scope",
    ],
}


def _function(module: str, name: str):
    return getattr(importlib.import_module(f"tests.{module}"), name, None)


@pytest.mark.parametrize("node", NODES)
def test_every_suite_node_is_a_module_level_test_the_image_can_run(node):
    path, _, name = node.partition("::")
    assert path.startswith("tests/test_") and path.endswith(".py"), node
    assert name.startswith("test_") and "::" not in name and "[" not in name, node
    function = _function(path[len("tests/"):-len(".py")], name)
    assert inspect.isfunction(function), node
    # The harness copies backend/tests into the image and nothing else from the repository.
    assert "REPO" not in inspect.getsource(function), node


def test_every_category_of_both_suites_is_filled():
    assert {(s.connector_id, s.capability) for s in SUITES.values()} == {
        ("paypal", "refunds.create"), ("internal-merchant", "merchant.payment_status.correct")}
    for suite in SUITES.values():
        assert suite.empty_categories() == [], suite.suite_id


@pytest.mark.parametrize("criterion", sorted(TRACE))
def test_every_traced_test_exists(criterion):
    for entry in TRACE[criterion]:
        module, name = entry.split("::")
        assert callable(_function(module, name)), entry


def test_every_criterion_is_traced():
    expected = (
        {f"AC-MERCH-{n:02}" for n in range(1, 8)}
        | {f"AC-AUTHZ-{n:02}" for n in (6, 8, 15, 16, 17)}
        | {f"AC-PLY-{n:02}" for n in (2, 8, 9, 10, 11)}
        | {"AC-PP-08", "AC-EXEC-MODE-03"}
    )
    assert set(TRACE) == expected


def test_each_suite_runs_the_traced_evidence_its_image_can_hold():
    """Every traced test is in a suite, except those reading repository files the image does
    not carry, which run in CI instead."""
    listed = {node.replace("tests/", "").replace(".py::", "::") for node in NODES}
    traced = {entry for entries in TRACE.values() for entry in entries}
    image_bound = {e for e in traced if "REPO" not in inspect.getsource(_function(*e.split("::")))}
    assert image_bound <= listed, sorted(image_bound - listed)
