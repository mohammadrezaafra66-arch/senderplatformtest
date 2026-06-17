import type { WorkerItem } from "../types";

interface WorkerTableProps {
  workers: WorkerItem[];
}

export function WorkerTable({ workers }: WorkerTableProps) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Last seen at</th>
          </tr>
        </thead>
        <tbody>
          {workers.length === 0 ? (
            <tr>
              <td colSpan={3} className="muted">
                No worker data
              </td>
            </tr>
          ) : (
            workers.map((worker) => (
              <tr key={worker.name}>
                <td>{worker.name}</td>
                <td>
                  <span className={`badge badge--${worker.status}`}>
                    {worker.status}
                  </span>
                </td>
                <td>{worker.last_seen_at ?? "—"}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
