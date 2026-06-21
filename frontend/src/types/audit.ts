export type AuditLogItem = {
  id: number;
  username: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  timestamp: string;
  details: Record<string, unknown> | null;
};

export type AuditLogListResult = {
  items: AuditLogItem[];
};
