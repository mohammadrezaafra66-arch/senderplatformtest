import type { QueueItem } from "../types";

interface QueueTableProps {
  queues: QueueItem[];
}

export function QueueTable({ queues }: QueueTableProps) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Queue name</th>
            <th>Pending</th>
          </tr>
        </thead>
        <tbody>
          {queues.length === 0 ? (
            <tr>
              <td colSpan={2} className="muted">
                No queue data
              </td>
            </tr>
          ) : (
            queues.map((queue) => (
              <tr key={queue.name}>
                <td>{queue.name}</td>
                <td>{queue.pending}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
