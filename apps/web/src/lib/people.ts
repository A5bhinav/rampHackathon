// The admin's People and Demo pages (spec 0095). Pure helpers, so they're tested without a server.
// The database decides every action; these only label what it returns.
import type { AdminLogEntry, Role } from "./api-types";

export const ROLES: Role[] = ["clerk", "approver", "auditor", "admin"];
export const ROLE_LABEL: Record<Role, string> = { clerk: "Clerk", approver: "Approver", auditor: "Auditor", admin: "Admin" };
/** The core's sentence for a person whose access an admin turned off (spec 0095, D7). */
export const ACCESS_OFF = "Your access is turned off. Ask an admin.";

export function roleLabels(roles: readonly string[]): string {
  return roles.map((r) => ROLE_LABEL[r as Role] ?? r).join(", ") || "No role";
}

/** Whether to offer the admin pages. A display choice: the database refuses non-admins anyway. */
export function isAdmin(roles: readonly string[]): boolean {
  return roles.includes("admin");
}

/** One line of the organisation chain, in words. The team's script (add_user.py) has no person. */
export function logLine(e: AdminLogEntry): string {
  const who = e.actor?.display_name ?? "The team (add_user.py)";
  const them = e.subject?.display_name ?? "someone";
  const d = e.detail as Record<string, unknown>;
  const role = typeof d.role === "string" ? ROLE_LABEL[d.role as Role] ?? d.role : "a";
  switch (e.type) {
    case "role_granted": return `${who} made ${them} ${/^[AEIOU]/.test(String(role)) ? "an" : "a"} ${role}`;
    case "role_revoked": return `${who} took the ${role} role from ${them}`;
    case "ramp_link_set": return d.ramp_user_id ? `${who} linked ${them} to a Ramp user` : `${who} cleared ${them}'s Ramp link`;
    case "access_disabled": return `${who} turned off ${them}'s access`;
    case "access_enabled": return `${who} turned ${them}'s access back on`;
    case "demo_reset": return `${who} reset the demo`;
    case "demo_chaos": return `${who} turned chaos ${d.enabled ? "on" : "off"}`;
    case "demo_tamper": return `${who} tampered with the demo's evidence chain`;
    default: return `${who}: ${e.type}`;
  }
}
