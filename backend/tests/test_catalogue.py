import pytest
import yaml

from app.authz.catalogue import (
    CatalogueError,
    get_catalogue,
    load_catalogue,
    load_permission_sets,
)

# TODO(G1 1.3 Rishav): owner review; any new core key fails this by design (spec revision, 22.2).
APPENDIX_A = """
platform.tenant.read platform.tenant.manage platform.connector.register
platform.domain_pack.register platform.ops.read platform.support.request
platform.support.approve platform.support.access platform.release.attest
tenant.read tenant.update tenant.delete tenant.ownership.transfer
member.read member.invite member.invitation.manage member.role.assign member.remove
role.read role.manage audit.read
connector.read source.read source.create source.update source.delete
source.credential.manage source.discover source.sync source.file.upload source.webhook.manage
source_record.read evidence.raw.read connector.action.invoke
domain_pack.read domain_pack.manage
ontology.read ontology.generate ontology.edit ontology.validate ontology.publish
mapping.read mapping.edit mapping.publish authority.read authority.edit
rule.read rule.edit policy.read policy.edit
domain_run.read domain_run.create entity.read entity_resolution.read entity_resolution.review
case.read case.triage case.investigate case.classify
investigation.read investigation.record domain.compute
playbook.read playbook.edit playbook.publish playbook.recommend playbook.validate
playbook.request playbook.approve playbook.reject playbook.execute
execution.read execution.record outcome.verify execution_mode.manage
""".split()


def sets_by_key():
    return {s.key: s for s in load_permission_sets(get_catalogue())}


def test_catalogue_matches_appendix_a_exactly():
    assert {spec.key for spec in get_catalogue()} == set(APPENDIX_A)


def test_unknown_key_fails_fast():
    with pytest.raises(CatalogueError):
        get_catalogue().get("playbook.yolo")


def test_pol_03_principal_ceilings():
    catalogue = get_catalogue()
    assert catalogue.get("playbook.approve").principal_types == ("human",)
    assert catalogue.get("platform.support.approve").principal_types == ("platform_human",)
    assert catalogue.get("playbook.execute").principal_types == ("worker",)
    assert catalogue.get("connector.action.invoke").principal_types == ("worker",)
    assert catalogue.get("platform.release.attest").principal_types == ("ci",)


def test_ceiling_violation_fails_load(tmp_path):
    bad = {
        "release": "x",
        "tenant": {
            "playbook.approve": [["human", "agent"], [], "critical", "d"],
            "playbook.execute": [["worker"], [], "critical", "d"],
            "connector.action.invoke": [["worker"], [], "critical", "d"],
        },
        "platform": {
            "platform.support.approve": [["platform_human"], [], "critical", "d"],
            "platform.release.attest": [["ci"], [], "critical", "d"],
        },
    }
    path = tmp_path / "permissions.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(CatalogueError, match="POL-03"):
        load_catalogue(path)


def test_owner_reserved_and_platform_keys_are_not_grantable():
    catalogue = get_catalogue()
    for key in ("tenant.delete", "tenant.ownership.transfer", "execution_mode.manage"):
        assert not catalogue.get(key).tenant_grantable
    assert not [s for s in catalogue if s.scope_type == "platform" and s.tenant_grantable]


def test_tenant_administration_has_no_narrowing():
    catalogue = get_catalogue()
    for spec in catalogue:
        if spec.key.split(".")[0] in {"tenant", "member", "role"}:
            assert spec.narrowable_scopes == (), spec.key


def test_owner_set_inherits_admin_without_action_rights():
    sets = sets_by_key()
    owner = set(sets["tenant_owner"].allow)
    assert set(sets["business_admin"].allow) < owner
    assert {"tenant.delete", "tenant.ownership.transfer", "execution_mode.manage"} <= owner
    assert not owner & {"playbook.request", "playbook.approve", "playbook.execute"}
    assert sets["tenant_owner"].derivation == "OWNER_POINTER"


def test_business_admin_cannot_touch_credentials_or_execution_mode():
    allow = set(sets_by_key()["business_admin"].allow)
    assert not allow & {"source.credential.manage", "source.webhook.manage",
                        "execution_mode.manage", "playbook.approve"}


def test_investigation_agent_cannot_act():
    allow = set(sets_by_key()["investigation_agent"].allow)
    assert not allow & {"playbook.request", "playbook.approve", "playbook.reject",
                        "playbook.execute", "connector.action.invoke"}
