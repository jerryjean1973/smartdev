"""Scoped SmartDev read tools for an authenticated external MCP transport."""

from uuid import UUID


class ScopedTaskReads:
    def __init__(self, project_ids, *, projects, task_navigation):
        self.project_ids = frozenset(UUID(str(value)) for value in project_ids)
        self.projects = projects
        self.task_navigation = task_navigation

    def list_projects(self):
        return [
            {"project_id": str(row["id"]), "name": row["name"], "status": row["status"]}
            for row in self.projects()
            if UUID(str(row["id"])) in self.project_ids and not row.get("archived")
        ]

    def _require_project(self, project_id):
        project_id = UUID(str(project_id))
        if not any(row["project_id"] == str(project_id) for row in self.list_projects()):
            raise LookupError("Project not available")
        return project_id

    def list_tasks(self, project_id, status=None):
        project_id = self._require_project(project_id)
        return [
            self._task(row) for row in self.task_navigation()
            if UUID(str(row["project_id"])) == project_id and
            (status is None or row["status"] == status)
        ]

    def get_task(self, task_id):
        task_id = UUID(str(task_id))
        for row in self.task_navigation():
            if UUID(str(row["id"])) == task_id:
                try:
                    self._require_project(row["project_id"])
                except LookupError:
                    break
                return self._task(row)
        raise LookupError("Task not available")

    @staticmethod
    def _task(row):
        return {
            "task_id": str(row["id"]), "project_id": str(row["project_id"]),
            "title": row["title"], "status": row["status"],
        }
