// The six core workspaces from spec 13.1.

export type WorkspaceDefinition = {
  path: string;
  title: string;
  permission: string;
  summary: string;
  tasks: string;
};

export const WORKSPACES: WorkspaceDefinition[] = [
  // TODO(G1 1.12 Suprava): build the Team screens: members, grants, invitations, transfer.
  {
    path: "/team",
    title: "Team",
    permission: "member.read",
    summary: "Members, grants, pending invitations and ownership transfer.",
    tasks: "1.11, 1.12",
  },
  {
    path: "/sources",
    title: "Sources",
    permission: "source.read",
    summary: "Connectors, declared capabilities, credential status, sync and file uploads.",
    tasks: "4.7",
  },
  {
    path: "/ontology",
    title: "Ontology Studio",
    permission: "ontology.read",
    summary: "Composition, source bindings, authority and exact-candidate publication.",
    tasks: "2.8, 2.9, 2.10",
  },
  {
    path: "/operations",
    title: "Domain Operations",
    permission: "domain_run.read",
    summary: "Runs, entities, observations, resolved values and scoped cases.",
    tasks: "3.9",
  },
  {
    path: "/investigations",
    title: "Investigation",
    permission: "investigation.read",
    summary: "Evidence-grounded analysis, tool trace and playbook recommendation.",
    tasks: "5.10",
  },
  {
    path: "/playbooks",
    title: "Playbook Requests",
    permission: "playbook.read",
    summary: "Requests, approvals, policy outcome, execution, verification and audit.",
    tasks: "5.11",
  },
];
