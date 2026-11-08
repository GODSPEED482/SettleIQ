import type { WorkspaceDefinition } from "./workspaces";

export function Workspace({ workspace }: { workspace: WorkspaceDefinition }) {
  return (
    <section>
      <h1>{workspace.title}</h1>
      <p>{workspace.summary}</p>
      <p className="muted">
        Placeholder. Built in plan tasks {workspace.tasks}. Requires{" "}
        <code>{workspace.permission}</code>.
      </p>
    </section>
  );
}
