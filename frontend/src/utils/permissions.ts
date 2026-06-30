import type { Role } from "@/state/auth";

export type NavItemKey =
  | "dashboard"
  | "accounts"
  | "contacts"
  | "campaigns"
  | "reports"
  | "settings"
  | "auditLogs";

export function canViewAudit(role: Role) {
  return role === "admin";
}

export function canViewSettings(role: Role) {
  return role === "admin";
}

export function canTriggerWhatsAppWarmup(role: Role) {
  return role === "admin";
}

export function canManageAccounts(role: Role) {
  return role === "admin";
}

export function canManageRubika(role: Role) {
  return role === "admin";
}

export function canUploadContacts(role: Role) {
  return role === "admin" || role === "operator";
}

export function canCreateCampaign(role: Role) {
  return role === "admin" || role === "operator";
}

export function canControlCampaign(role: Role) {
  return role === "admin" || role === "operator";
}

export function canViewCampaigns(role: Role) {
  return role === "admin" || role === "operator";
}

export function canViewMessageLogs(role: Role) {
  return role === "admin" || role === "operator" || role === "viewer";
}

